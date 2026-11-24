# SETUP.md -- Installation and Model Download

## Prerequisites

- Python 3.12 or later
- `uv` or `pip`
- Tesseract OCR (for scanned document support)
- Poppler (required by pdf2image for scanned PDF rendering)

## Step 1: Create Virtual Environment

```bash
cd TenderExtractPro
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/macOS: source venv/bin/activate
```

## Step 2: Install Dependencies

```bash
python -m pip install -r requirements.txt
```

## Step 3: Configure Environment

The project loads settings from the root `.env` file. Add your Groq API key to
`GROQ_API_KEY`; do not commit it. The matching template is `.env.example`.

### CUDA-enabled installs

PaddleOCR is configured for `gpu:0`. Install the CUDA 12.6 PaddlePaddle build:

```bash
python -m pip install paddlepaddle-gpu -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
```

The OCR pipeline will report an error if GPU PaddlePaddle is not available; it
does not silently switch to CPU. In Docker the CPU build (`paddlepaddle`) is
installed automatically with `PADDLEOCR_DEVICE=cpu`.

Embeddings use `BAAI/bge-m3` (~2 GB download on first run) via PyTorch. Install
the CUDA 12.6 build so embeddings and the optional Qwen2-VL fallback run on GPU:

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cu126
```

## Step 4: Install Poppler (for scanned PDFs)

**Windows:**
Download from: https://github.com/oschwartz10612/poppler-windows/releases

Extract and add the `bin/` folder to your system PATH.

**Linux:**
```bash
sudo apt-get install poppler-utils
```

## Step 5: Configure the Groq LLM

The default `.env` uses Groq's OpenAI-compatible API with the `openai/gpt-oss-120b`
model. Set `GROQ_API_KEY` to your key before starting the API.

## Step 6: Optional Local LLM

To use the previous local Mistral backend instead, set `LLM_PROVIDER=local` and
`LLM_MODEL_PATH` to a downloaded GGUF model. `llama-cpp-python` must be installed
with a CUDA-enabled build for GPU offload.

## Step 7: Optional Multimodal Table Fallback

For OCR-heavy or visually complex tables, enable the optional Qwen2-VL path.

Recommended baseline for 8 GB VRAM-class devices:

```bash
set MULTIMODAL_ENABLED=1
set MULTIMODAL_MODEL_NAME=Qwen/Qwen2-VL-2B-Instruct
set MULTIMODAL_LOCAL_ONLY=1
```

Download the model into the Hugging Face cache first, or point `MULTIMODAL_MODEL_PATH` at a local model directory.

The code loads the vision model only during table extraction and releases it before the Mistral text extraction stage.

## Step 8: Verify Installation

Run the individual module smoke tests:

```bash
python -m tender_extraction.ingestion
python -m tender_extraction.table_extraction
python -m tender_extraction.chunking
python -m tender_extraction.retrieval
python -m tender_extraction.schemas
python -m tender_extraction.validation
```

Stages 1-4 and validation run without the LLM. The extraction smoke test requires
a configured and reachable LLM:

```bash
python -m tender_extraction.extraction
```

## Step 9: Run the Full Pipeline

```bash
python -m tender_extraction.main dataset/globaltender1576.pdf -o out.json --verbose
```

Inspect the output:

```bash
python -m json.tool out.json | head -80
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `groq` | LLM backend (`groq` or `local`) |
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible API endpoint |
| `LLM_MODEL` | `openai/gpt-oss-120b` | Model served by Groq |
| `GROQ_API_KEY` | unset | Groq API key (keep private) |
| `LLM_MODEL_PATH` | `models/mistral-7b-instruct-v0.2.Q4_K_M.gguf` | Local GGUF model path when `LLM_PROVIDER=local` |
| `OCR_ENGINE` | `paddleocr` | OCR engine (`paddleocr` or `tesseract`) |
| `PADDLEOCR_DEVICE` | `gpu:0` | PaddlePaddle inference device (`cpu` in Docker) |
| `PADDLEOCR_LANG` | `en` | PaddleOCR language |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | SentenceTransformer model for dense retrieval (1024-dim, no query prefix) |
| `REDIS_URL` | unset | Redis connection for the match/QA cache; unset = cache disabled |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Comma-separated allowed browser origins |
| `HF_HOME` | unset | HuggingFace cache directory (point it off a full C: drive) |
| `MULTIMODAL_ENABLED` | `0` | Enable Qwen2-VL table fallback for OCR/structure-heavy pages |
| `MULTIMODAL_MODEL_NAME` | `Qwen/Qwen2-VL-2B-Instruct` | Hugging Face model ID for the vision extractor |
| `MULTIMODAL_MODEL_PATH` | unset | Optional local path to the Qwen2-VL model directory |
| `MULTIMODAL_LOCAL_ONLY` | `1` | Avoid surprise model downloads during runtime |
| `MULTIMODAL_USE_GPU` | `1` | Prefer CUDA for the optional vision model when available |
| `MULTIMODAL_MAX_NEW_TOKENS` | `768` | Generation limit for optional table recovery |
| `MULTIMODAL_RENDER_SCALE` | `2.0` | Scale used to render pages for optional table recovery |
| `MULTIMODAL_RETRY_EMPTY_PAGES` | `1` | Retry table extraction on OCR or table-like pages |
| `REQUIRE_GPU` | `1` | Log when GPU acceleration is unavailable |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
