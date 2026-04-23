from anthropic import Anthropic
from autocontext.integrations.anthropic import instrument_client
client = instrument_client(Anthropic())
