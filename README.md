# TracePilot

A basic implementation of "TracePilot: Self-verifiable Framework for Decentralized Applications Fault Localization across Transactions".



# Installation

Please ensure your environment meets the following requirements:

- Python >= 3.9
- Node.js == 20.9

Use the following commands to install the dependencies for TracePilot:

```bash
pip install -r requirements.txt
npm install
pip install eth-heimdall
```



# Quick Start

Let's try to locate the [SushiSwap fault](https://rekt.news/badgers-digg-sushi/). Perform fault localization with TracePilot using the following steps:

1.  **Configuration**: Open the `settings.py` file and configure your **Tenderly API Key** and the information for the **Large Language Model (LLM)** you intend to access (including model name, API endpoint/URL, and API Key).
2.  **Data Preparation**: Move the `SushiSwap.json` file from the `benchmark` folder into the `dataset/raw` directory.
3.  **Execution**: Run the main entry script using the command below:

```bash
$ python main.py
```

> **Note on Output:**
> During execution, the console will display a brief summary of the agent interaction processes. Detailed execution logs for each agent (including full inputs and outputs) are automatically saved in the `agents/logs` folder.

# File Description

Below is a detailed description of the project structure and files:

- `agents`: Contains the implementation code for various agents and their corresponding execution logs.
- `benchmark`: A benchmark dataset consisting of 149 DApp fault cases.
- `compiler`: Contains various versions of the `solc` compiler.
- `daos`: The data access objects (DAO) used for data processing.
- `dataset`: The directory path for storing fault cases awaiting processing.
- `downloader`: Scripts to download data from blockchain APIs.
- `entity`: Definitions of entity class objects.
- `experiment_result`: Stores execution results and experimental data.
  - `pilot_report`: Verified reports generated after TracePilot execution.
  - `metric_logs`: Detailed records of the runtime process.
  - `experiments.csv`: A summary CSV containing detailed statistical results and report content.
- `mcp_tools`: Implementation code for the Model Context Protocol (MCP) toolset.
- `misc`: Necessary JavaScript files, such as scripts for injecting RPC interfaces.
- `process`: Core code for data processing logic.
- `prompt`: Contains all prompt templates used by the LLMs.
- `utils`: Implementation of utility tools used throughout TracePilot's execution.
- `concurrent_manager.py`: The manager script for handling concurrent startups.
- `main.py`: The entry point for the processing flow of a single case.
- `worker.py`: The launcher for a single case during concurrent execution processes.
