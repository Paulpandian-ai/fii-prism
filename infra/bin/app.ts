#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { NetworkStack } from "../lib/network-stack";
import { DataStack } from "../lib/data-stack";
import { AuthStack } from "../lib/auth-stack";
import { ComputeStack } from "../lib/compute-stack";
import { ApiStack } from "../lib/api-stack";
import { FrontendStack } from "../lib/frontend-stack";

const app = new cdk.App();

const envName = (app.node.tryGetContext("env") as string) ?? "dev";
const account = process.env.CDK_DEFAULT_ACCOUNT;
const region = process.env.CDK_DEFAULT_REGION ?? "us-east-1";
const env = { account, region };

const prefix = `FiiPrism-${envName}`;

// Tag everything so cost explorer can slice by project/env without guesswork.
cdk.Tags.of(app).add("Project", "fii-prism");
cdk.Tags.of(app).add("Environment", envName);
cdk.Tags.of(app).add("ManagedBy", "cdk");

const network = new NetworkStack(app, `${prefix}-Network`, { env, envName });

const data = new DataStack(app, `${prefix}-Data`, {
  env,
  envName,
  vpc: network.vpc,
});

const auth = new AuthStack(app, `${prefix}-Auth`, { env, envName });

const compute = new ComputeStack(app, `${prefix}-Compute`, {
  env,
  envName,
  vpc: network.vpc,
  dbSecret: data.dbSecret,
  dbSecurityGroup: data.dbSecurityGroup,
});

new ApiStack(app, `${prefix}-Api`, {
  env,
  envName,
  vpc: network.vpc,
  fargateService: compute.fargateService,
});

new FrontendStack(app, `${prefix}-Frontend`, { env, envName });

// Outputs wired across stacks via cross-stack references; Auth pool ID is consumed later.
new cdk.CfnOutput(auth, "UserPoolIdExport", {
  value: auth.userPool.userPoolId,
  exportName: `${prefix}-UserPoolId`,
});
