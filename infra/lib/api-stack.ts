import * as cdk from "aws-cdk-lib";
import * as apigatewayv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as integrations from "aws-cdk-lib/aws-apigatewayv2-integrations";
import type * as ec2 from "aws-cdk-lib/aws-ec2";
import type * as ecsPatterns from "aws-cdk-lib/aws-ecs-patterns";
import type { Construct } from "constructs";

export interface ApiStackProps extends cdk.StackProps {
  envName: string;
  vpc: ec2.IVpc;
  fargateService: ecsPatterns.ApplicationLoadBalancedFargateService;
}

/**
 * API Gateway HTTP API in front of the ALB. This gives us:
 * - A stable domain independent of the ALB DNS
 * - Usage-plan + JWT authorizer hooks (we'll wire Cognito in a later section)
 * - Lower cold-start cost than pointing CloudFront directly at the ALB
 */
export class ApiStack extends cdk.Stack {
  public readonly httpApi: apigatewayv2.HttpApi;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    const integration = new integrations.HttpAlbIntegration(
      "AlbIntegration",
      props.fargateService.listener,
      { method: apigatewayv2.HttpMethod.ANY },
    );

    this.httpApi = new apigatewayv2.HttpApi(this, "HttpApi", {
      apiName: `fii-prism-${props.envName}`,
      defaultIntegration: integration,
      corsPreflight: {
        allowOrigins: ["http://localhost:3000"],
        allowMethods: [apigatewayv2.CorsHttpMethod.ANY],
        allowHeaders: ["content-type", "authorization"],
      },
    });

    new cdk.CfnOutput(this, "HttpApiUrl", {
      value: this.httpApi.apiEndpoint,
    });
  }
}
