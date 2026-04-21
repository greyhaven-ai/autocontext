from openai import OpenAI
client = OpenAI()
response = client.chat.completions.create(model="gpt-4o", messages=[])
