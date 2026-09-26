import boto3

session = boto3.Session(profile_name="alexa-ai-user", region_name="us-west-2")
client = session.client("bedrock-runtime")

response = client.converse(
    modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    messages=[{"role": "user", "content": [{"text": "Reply with one word: ready"}]}],
    inferenceConfig={"maxTokens": 20},
)

print(response["output"]["message"]["content"][0]["text"])