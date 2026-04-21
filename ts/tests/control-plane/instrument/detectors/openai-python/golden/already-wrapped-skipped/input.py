from openai import OpenAI
from autocontext.integrations.openai import instrument_client
client = instrument_client(OpenAI())
