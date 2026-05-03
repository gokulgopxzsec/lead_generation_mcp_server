import asyncio
from browser_use import Agent
from langchain_ollama import ChatOllama

# Create a custom class that inherits from ChatOllama
# This natively satisfies browser-use without triggering Pydantic errors
class BrowserOllama(ChatOllama):
    @property
    def provider(self) -> str:
        return "ollama"

async def run_local_scraper():
    print("🔌 Connecting to local Ollama (qwen3:8b)...")
    
    # Use our safe, custom class
    llm = BrowserOllama(
        model="qwen3:8b", 
        temperature=0.0,
    )

    task_prompt = """
    Go to duckduckgo.com.
    Search for 'dental clinics in Thrissur Kerala'.
    Look at the search results. 
    Extract the names and phone numbers of the clinics.
    Format your final output as a clear list.
    """

    # We disable telemetry and gif generation to reduce library overhead
    agent = Agent(
        task=task_prompt,
        llm=llm,
        generate_gif=False, 
    )
    
    print("🤖 Local Agent is taking over the browser. Watch it work...")
    result = await agent.run()
    
    print("\n✅ Final Result from AI:")
    print(result.final_result())

if __name__ == "__main__":
    asyncio.run(run_local_scraper())