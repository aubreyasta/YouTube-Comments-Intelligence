import config
config.GEMINI_API_KEY = 'AQ.Ab8RN6KLKg3uthkZPnqSdib6htcjYF3aeE8IKLBUpYyedXUMzw'
from pipeline import llm
print(llm.ask('say hello'))
