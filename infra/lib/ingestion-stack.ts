import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import type * as s3 from "aws-cdk-lib/aws-s3";
import type * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as path from "path";
import type { Construct } from "constructs";

export interface IngestionStackProps extends cdk.StackProps {
  envName: string;
  vpc: ec2.IVpc;
  cluster: ecs.ICluster;
  dbSecret: secretsmanager.ISecret;
  dbSecurityGroup: ec2.SecurityGroup;
  rawDataBucket: s3.IBucket;
}

/**
 * Scheduled ingestion jobs run on Fargate. Each job is a single `fii-ingest <cmd>` invocation
 * against the same container image, with command-override per schedule.
 *
 * Section 2 ships the task definitions and the ECR asset wired, but only the AAPL seed
 * schedule is activated. Activate others by toggling `enabledSchedules` in app context.
 */
export class IngestionStack extends cdk.Stack {
  public readonly image: ecs.ContainerImage;
  public readonly taskDefinition: ecs.FargateTaskDefinition;

  constructor(scope: Construct, id: string, props: IngestionStackProps) {
    super(scope, id, props);

    const logGroup = new logs.LogGroup(this, "IngestLogs", {
      logGroupName: `/fii-prism/${props.envName}/ingest`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy:
        props.envName === "prod" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    const taskRole = new iam.Role(this, "IngestTaskRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      description: "Runtime role for fii-ingest Fargate tasks",
    });
    props.dbSecret.grantRead(taskRole);
    props.rawDataBucket.grantReadWrite(taskRole);
    // Bedrock invoke for the Titan embeddings fallback.
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel"],
        resources: [
          `arn:aws:bedrock:${cdk.Aws.REGION}::foundation-model/amazon.titan-embed-text-v2:0`,
        ],
      }),
    );

    const repoRoot = path.resolve(__dirname, "..", "..");
    this.image = ecs.ContainerImage.fromAsset(repoRoot, {
      file: "apps/ingest/Dockerfile",
      platform: ecrAssets.Platform.LINUX_AMD64,
    });

    this.taskDefinition = new ecs.FargateTaskDefinition(this, "IngestTaskDef", {
      cpu: 1024,
      memoryLimitMiB: 2048,
      family: `fii-prism-ingest-${props.envName}`,
      taskRole,
    });
    const container = this.taskDefinition.addContainer("ingest", {
      image: this.image,
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: "ingest", logGroup }),
      environment: {
        NEXT_PUBLIC_APP_ENV: props.envName,
        LOG_LEVEL: "info",
        AWS_REGION: cdk.Stack.of(this).region,
        RAW_DATA_BUCKET: props.rawDataBucket.bucketName,
      },
      secrets: {
        POSTGRES_USER: ecs.Secret.fromSecretsManager(props.dbSecret, "username"),
        POSTGRES_PASSWORD: ecs.Secret.fromSecretsManager(props.dbSecret, "password"),
        POSTGRES_HOST: ecs.Secret.fromSecretsManager(props.dbSecret, "host"),
        POSTGRES_PORT: ecs.Secret.fromSecretsManager(props.dbSecret, "port"),
        POSTGRES_DB: ecs.Secret.fromSecretsManager(props.dbSecret, "dbname"),
        // Provider keys — populated from Secrets Manager (manually provisioned once).
        POLYGON_API_KEY: ecs.Secret.fromSecretsManager(
          this._provider_secret("POLYGON_API_KEY", props.envName),
        ),
        FMP_API_KEY: ecs.Secret.fromSecretsManager(
          this._provider_secret("FMP_API_KEY", props.envName),
        ),
        FINNHUB_API_KEY: ecs.Secret.fromSecretsManager(
          this._provider_secret("FINNHUB_API_KEY", props.envName),
        ),
        FRED_API_KEY: ecs.Secret.fromSecretsManager(
          this._provider_secret("FRED_API_KEY", props.envName),
        ),
        VOYAGE_API_KEY: ecs.Secret.fromSecretsManager(
          this._provider_secret("VOYAGE_API_KEY", props.envName),
        ),
      },
    });

    // Security group for all ingest tasks; open 5432 to the DB via L1 ingress rule on the
    // DB SG (same pattern as compute-stack to avoid a cycle).
    const ingestSg = new ec2.SecurityGroup(this, "IngestSg", {
      vpc: props.vpc,
      description: "fii-ingest Fargate tasks",
      allowAllOutbound: true,
    });
    new ec2.CfnSecurityGroupIngress(this, "AuroraIngressFromIngest", {
      groupId: props.dbSecurityGroup.securityGroupId,
      sourceSecurityGroupId: ingestSg.securityGroupId,
      ipProtocol: "tcp",
      fromPort: 5432,
      toPort: 5432,
      description: "Ingest Fargate → Aurora",
    });

    // Schedule registry — enabled per envName context key to avoid running all jobs in dev.
    const enabledSchedules = new Set<string>(
      (this.node.tryGetContext("ingest:enabledSchedules") as string[] | undefined) ?? [
        "seed-aapl",
      ],
    );

    const addSchedule = (
      id: string,
      schedule: events.Schedule,
      command: string[],
      description: string,
    ) => {
      if (!enabledSchedules.has(id)) return;
      new events.Rule(this, `Rule${id}`, {
        description,
        schedule,
        enabled: true,
        targets: [
          new targets.EcsTask({
            cluster: props.cluster,
            taskDefinition: this.taskDefinition,
            subnetSelection: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
            securityGroups: [ingestSg],
            assignPublicIp: false,
            containerOverrides: [
              {
                containerName: container.containerName,
                command,
              },
            ],
          }),
        ],
      });
    };

    // Seed AAPL once daily at 5am UTC (midnight ET).
    addSchedule(
      "seed-aapl",
      events.Schedule.cron({ minute: "0", hour: "5" }),
      ["seed", "--ticker", "AAPL", "--full"],
      "Daily full-refresh seed for AAPL (MVP scope)",
    );

    // Reference-only schedules — add to `ingest:enabledSchedules` context to turn on.
    addSchedule(
      "prices-daily-watchlist",
      events.Schedule.cron({ minute: "0", hour: "1" }),
      ["prices-daily", "--ticker", "AAPL"],
      "Nightly EOD price refresh (watchlist)",
    );
    addSchedule(
      "macro-daily",
      events.Schedule.cron({ minute: "30", hour: "5" }),
      ["macro"],
      "Daily FRED macro refresh",
    );
    addSchedule(
      "news-15m",
      events.Schedule.rate(cdk.Duration.minutes(15)),
      ["news", "--ticker", "AAPL", "--days-back", "1"],
      "15-min news refresh (watchlist)",
    );
    addSchedule(
      "insiders-daily",
      events.Schedule.cron({ minute: "0", hour: "6" }),
      ["insiders", "--ticker", "AAPL"],
      "Daily Form 4 insider refresh",
    );

    new cdk.CfnOutput(this, "IngestTaskDefinitionArn", {
      value: this.taskDefinition.taskDefinitionArn,
    });
    new cdk.CfnOutput(this, "IngestClusterArn", { value: props.cluster.clusterArn });
  }

  private _provider_secret(
    name: string,
    envName: string,
  ): secretsmanager.ISecret {
    // Reference a Secrets Manager secret by name, expected to be manually created once
    // per env (we don't create it in CDK to avoid CloudFormation storing a placeholder
    // value). Lookup by name; CDK will pass the ARN to the task at deploy time.
    const secretName = `fii-prism/${envName}/providers/${name}`;
    const secretsmanager = require("aws-cdk-lib/aws-secretsmanager") as typeof import("aws-cdk-lib/aws-secretsmanager");
    return secretsmanager.Secret.fromSecretNameV2(this, `Secret${name}`, secretName);
  }
}
