# CAD SIS 3 — Orders service

The HTTP API runs on two ECS Fargate tasks behind a public ALB. The tasks are in private subnets across two availability zones and use one NAT gateway for outbound traffic. The ECS execution role pulls images from ECR and writes container logs. The separate task role publishes to SNS and reads orders from DynamoDB.

POST /orders publishes OrderCreated and immediately returns HTTP 202. SNS delivers the event to SQS. Lambda writes to DynamoDB with a conditional insert on orderId. A duplicate leaves the original record intact. Invalid events are retried and go to the DLQ. ECS and Lambda logs have seven-day retention.

## Deploy

Requires AWS CLI, Docker and AWS access to provision CloudFormation resources and IAM roles. In AWS CloudShell, keep the region at eu-north-1. From the project directory:

```bash
bash deploy.sh cad-sis3-main dev
```

The command creates the ECR stack, builds and pushes the image with an immutable source-derived tag, then deploys the app stack using the image digest. CloudFormation defines the repository, networking, ECS, ALB, IAM, logging and messaging. Docker builds and uploads the image. Use separate stack names for independent copies.

## Verify

```bash
python3 -u verify.py cad-sis3-main --dlq
```

Run with boto3 installed. The script checks the public health endpoint, two healthy targets, an accepted order saved to DynamoDB, an ignored duplicate with a different SQS message ID, and delivery of an invalid event to the DLQ. The last check can take several minutes because the queue waits between retries.

## Repeat from a clean checkout

Commit the source to your course Git repository. From a fresh clone, run:

```bash
bash deploy.sh cad-sis3-copy test
python3 -u verify.py cad-sis3-copy
```

## Teardown

After the defense, delete each app stack before its image repository stack. These stacks contain disposable lab data. The app deletion deletes the test orders, and repository deletion removes its images.

```bash
export AWS_DEFAULT_REGION=eu-north-1
for stack in cad-sis3-copy cad-sis3-main; do
  aws cloudformation delete-stack --stack-name "$stack" &&
    aws cloudformation wait stack-delete-complete --stack-name "$stack" &&
    aws cloudformation delete-stack --stack-name "$stack-ecr" &&
    aws cloudformation wait stack-delete-complete --stack-name "$stack-ecr" || break
done
```
