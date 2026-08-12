from openai import OpenAI
client = OpenAI()
response = client.chat.completions.create(model="gpt-5.6-terra", messages=[])
