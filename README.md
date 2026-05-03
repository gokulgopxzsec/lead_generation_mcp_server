# Lead Generation MCP Server 🔍🚀

An automated lead generation and enrichment engine built on the **Model Context Protocol (MCP)**. This server allows AI agents (like Claude) to find businesses, scrape contact details, and enrich leads directly through a chat interface.

## 🌟 Features

*   **Business Scraping**: Find businesses by niche and location (e.g., "Dental clinics in Thrissur").
*   **Contact Enrichment**: Automatically search for missing phone numbers, emails, and social profiles.
*   **Source Integration**: Pulls data from DuckDuckGo, Justdial, Sulekha, and direct website crawling.
*   **Stealth Execution**: Designed to bypass common bot detection for high-quality data retrieval.

## 🛠 Tools Provided

| Tool Name | Description | Input Parameters |
| :--- | :--- | :--- |
| `scrape_leads` | Finds new businesses based on category and city. | `query`, `location` |
| `enrich_lead` | Attempts to find contact info for a specific business ID. | `lead_id` |
| `export_leads` | Exports collected data to CSV or JSON format. | `format` |

## 🚀 Getting Started

### Prerequisites
*   Python 3.10+
*   An MCP-compatible client (e.g., [Claude Desktop](https://claude.ai/download))

### Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/yourusername/lead-generation-mcp-server.git](https://github.com/yourusername/lead-generation-mcp-server.git)
   cd lead-generation-mcp-server