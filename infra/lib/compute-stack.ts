import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecsPatterns from "aws-cdk-lib/aws-ecs-patterns";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import type * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as path from "path";
import type { Construct } from "constructs";

export interface ComputeStackProps extends cdk.StackProps {
  envName: string;
  vpc: ec2.IVpc;
  dbSecret: secretsmanager.ISecret;
  dbSecurityGroup: ec2.SecurityGroup;
}

/**
 * ECS Fargate cluster + ApplicationLoadBalancedFargateService for the FastAPI.
 *
 * Section 1 intentionally builds an empty-but-wired compute plane. When the Dockerfile
 * at apps/api/Dockerfile is ready (it is), cdk will build and push to ECR on deploy.
 */
export class ComputeStack extends cdk.Stack {
  public readonly cluster: ecs.Cluster;
  public readonly fargateService: ecsPatterns.ApplicationLoadBalancedFargateService;

  constructor(scope: Construct, id: string, props: ComputeStackProps) {
    super(scope, id, props);

    this.cluster = new ecs.Cluster(this, "Cluster", {
      vpc: props.vpc,
      clusterName: `fii-prism-${props.envName}`,
      containerInsights: true,
    });

    const logGroup = new logs.LogGroup(this, "ApiLogs", {
      logGroupName: `/fii-prism/${props.envName}/api`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy:
        props.envName === "prod" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    const taskRole = new iam.Role(this, "ApiTaskRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
      description: "Runtime role for the FII-PRISM FastAPI Fargate tasks",
    });
    props.dbSecret.grantRead(taskRole);

    // Repo root is two levels up from infra/lib; Dockerfile references paths from the root.
    const repoRoot = path.resolve(__dirname, "..", "..");

    this.fargateService = new ecsPatterns.ApplicationLoadBalancedFargateService(this, "ApiService", {
      cluster: this.cluster,
      serviceName: `fii-prism-api-${props.envName}`,
      cpu: 512,
      memoryLimitMiB: 1024,
      desiredCount: 1,
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
      publicLoadBalancer: true,
      assignPublicIp: false,
      taskSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      taskImageOptions: {
        image: ecs.ContainerImage.fromAsset(repoRoot, {
          file: "apps/api/Dockerfile",
          platform: ecrAssets.Platform.LINUX_AMD64,
        }),
        containerName: "api",
        containerPort: 8000,
        taskRole,
        logDriver: ecs.LogDrivers.awsLogs({ streamPrefix: "api", logGroup }),
        environment: {
          NEXT_PUBLIC_APP_ENV: props.envName,
          LOG_LEVEL: "info",
          AWS_REGION: cdk.Stack.of(this).region,
        },
        // Aurora's generated secret is JSON — inject individual fields as env vars.
        // The API composes DATABASE_URL from these at boot (see app/config.py).
        secrets: {
          POSTGRES_USER: ecs.Secret.fromSecretsManager(props.dbSecret, "username"),
          POSTGRES_PASSWORD: ecs.Secret.fromSecretsManager(props.dbSecret, "password"),
          POSTGRES_HOST: ecs.Secret.fromSecretsManager(props.dbSecret, "host"),
          POSTGRES_PORT: ecs.Secret.fromSecretsManager(props.dbSecret, "port"),
          POSTGRES_DB: ecs.Secret.fromSecretsManager(props.dbSecret, "dbname"),
        },
      },
    });

    // Aurora ingress: Fargate SG → 5432.
    // Creating the ingress as an L1 CfnSecurityGroupIngress in THIS stack breaks the
    // cycle that would form if we called props.dbSecurityGroup.addIngressRule() (which
    // places the rule in the Data stack, making Data depend on Compute).
    const fargateSg = this.fargateService.service.connections.securityGroups[0]!;
    new ec2.CfnSecurityGroupIngress(this, "AuroraIngressFromApi", {
      groupId: props.dbSecurityGroup.securityGroupId,
      sourceSecurityGroupId: fargateSg.securityGroupId,
      ipProtocol: "tcp",
      fromPort: 5432,
      toPort: 5432,
      description: "API Fargate → Aurora",
    });

    this.fargateService.targetGroup.configureHealthCheck({
      path: "/health",
      healthyHttpCodes: "200",
      interval: cdk.Duration.seconds(30),
    });

    new cdk.CfnOutput(this, "ApiUrl", {
      value: `http://${this.fargateService.loadBalancer.loadBalancerDnsName}`,
    });
  }
}
