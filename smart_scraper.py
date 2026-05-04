import asyncio
from langchain_ollama import ChatOllama
from browser_use import Agent

# A lightweight wrapper to satisfy browser-use's telemetry checks
class BrowserLLMWrapper:
    def __init__(self, llm):
        self.llm = llm
        self.provider = 'ollama'  # Satisfies line 235 in service.py
        self.model_name = getattr(llm, 'model', 'qwen3:8b')

    async def ainvoke(self, *args, **kwargs):
        return await self.llm.ainvoke(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.llm, name)

async def run_local_scraper():
    raw_llm = ChatOllama(
        model="qwen3:8b",
        temperature=0, 
        base_url="http://127.0.0.1:11434",
        format="json",  # CRITICAL: Forces Ollama to strictly output JSON
        num_ctx=8192
    )

    # Wrap the standard LLM
    llm = BrowserLLMWrapper(raw_llm)

    task_prompt = "Go to google.com, type 'AI news' in the search box, and press enter."

    agent = Agent(
        task=task_prompt,
        llm=llm,
        use_vision=False, # Essential for local 8B models
        generate_gif=False,
    )

    await agent.run()

if __name__ == '__main__':
    asyncio.run(run_local_scraper())