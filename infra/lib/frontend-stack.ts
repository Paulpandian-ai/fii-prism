import * as cdk from "aws-cdk-lib";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as s3 from "aws-cdk-lib/aws-s3";
import type { Construct } from "constructs";

export interface FrontendStackProps extends cdk.StackProps {
  envName: string;
}

/**
 * Static export of the Next.js app lives in S3, fronted by CloudFront with OAC.
 * `pnpm --filter @fii/web build` produces ./apps/web/out which we'll sync to this bucket
 * from CI after deploy. (We don't deploy contents from CDK to keep IaC synth-deterministic.)
 */
export class FrontendStack extends cdk.Stack {
  public readonly bucket: s3.Bucket;
  public readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    this.bucket = new s3.Bucket(this, "WebBucket", {
      bucketName: `fii-prism-${props.envName}-web-${cdk.Aws.ACCOUNT_ID}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy:
        props.envName === "prod" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: props.envName !== "prod",
    });

    this.distribution = new cloudfront.Distribution(this, "WebDistribution", {
      comment: `FII-PRISM web (${props.envName})`,
      defaultRootObject: "index.html",
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.bucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        compress: true,
      },
      errorResponses: [
        // Next.js static export uses trailingSlash, but SPA-style 404 fallback helps client routes.
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: "/index.html" },
      ],
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
    });

    new cdk.CfnOutput(this, "WebBucketName", { value: this.bucket.bucketName });
    new cdk.CfnOutput(this, "WebDistributionDomain", {
      value: this.distribution.distributionDomainName,
    });
    new cdk.CfnOutput(this, "WebDistributionId", { value: this.distribution.distributionId });
  }
}
