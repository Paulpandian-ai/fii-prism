import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as rds from "aws-cdk-lib/aws-rds";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import type { Construct } from "constructs";

export interface DataStackProps extends cdk.StackProps {
  envName: string;
  vpc: ec2.IVpc;
}

/**
 * Aurora PostgreSQL Serverless v2 (0.5 → 4 ACU) + S3 buckets for reasoning trails and raw data.
 *
 * pgvector is enabled on Aurora via the default parameter group for Postgres 16.
 * Section 2 will add the CREATE EXTENSION step in the first real migration.
 */
export class DataStack extends cdk.Stack {
  public readonly cluster: rds.DatabaseCluster;
  public readonly dbSecret: secretsmanager.ISecret;
  public readonly dbSecurityGroup: ec2.SecurityGroup;
  public readonly reasoningTrailsBucket: s3.Bucket;
  public readonly rawDataBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);

    this.dbSecurityGroup = new ec2.SecurityGroup(this, "DbSecurityGroup", {
      vpc: props.vpc,
      description: "Aurora cluster ingress — Fargate tasks only",
      allowAllOutbound: false,
    });

    this.cluster = new rds.DatabaseCluster(this, "Aurora", {
      engine: rds.DatabaseClusterEngine.auroraPostgres({
        version: rds.AuroraPostgresEngineVersion.VER_16_4,
      }),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      serverlessV2MinCapacity: 0.5,
      serverlessV2MaxCapacity: 4,
      writer: rds.ClusterInstance.serverlessV2("writer"),
      readers: [],
      securityGroups: [this.dbSecurityGroup],
      defaultDatabaseName: "fii_prism",
      credentials: rds.Credentials.fromGeneratedSecret("fii_admin", {
        secretName: `fii-prism/${props.envName}/db`,
      }),
      backup: {
        retention: cdk.Duration.days(props.envName === "prod" ? 14 : 3),
      },
      removalPolicy:
        props.envName === "prod" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      storageEncrypted: true,
    });
    this.dbSecret = this.cluster.secret!;

    this.reasoningTrailsBucket = new s3.Bucket(this, "ReasoningTrails", {
      bucketName: `fii-prism-${props.envName}-reasoning-trails-${cdk.Aws.ACCOUNT_ID}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      versioned: true,
      enforceSSL: true,
      removalPolicy:
        props.envName === "prod" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: props.envName !== "prod",
      lifecycleRules: [
        {
          id: "transition-to-ia",
          transitions: [
            {
              storageClass: s3.StorageClass.INFREQUENT_ACCESS,
              transitionAfter: cdk.Duration.days(30),
            },
          ],
        },
      ],
    });

    this.rawDataBucket = new s3.Bucket(this, "RawData", {
      bucketName: `fii-prism-${props.envName}-raw-data-${cdk.Aws.ACCOUNT_ID}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      versioned: false,
      enforceSSL: true,
      removalPolicy:
        props.envName === "prod" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: props.envName !== "prod",
    });

    new cdk.CfnOutput(this, "DbEndpoint", { value: this.cluster.clusterEndpoint.hostname });
    new cdk.CfnOutput(this, "DbSecretArn", { value: this.dbSecret.secretArn });
    new cdk.CfnOutput(this, "ReasoningTrailsBucket", {
      value: this.reasoningTrailsBucket.bucketName,
    });
  }
}
