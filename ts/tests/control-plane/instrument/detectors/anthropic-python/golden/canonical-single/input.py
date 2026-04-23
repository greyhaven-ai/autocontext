from anthropic import Anthropic
client = Anthropic()
response = client.messages.create(model="claude-opus-4-5", messages=[])
