"""
Quil TTT Worker — Test-Time Training / EAFT Background Process
==============================================================
Monitors ~/.quil/ttt_queue.jsonl for training pairs queued by _eaft_gate().
When the batch reaches TRAIN_BATCH_SIZE, triggers a LoRA fine-tune via
Apple's MLX framework (mlx-lm) using the locally installed Qwen3-4B model.

Requirements:
  pip install mlx-lm

Run as a separate process (Tauri Sidecar or background daemon):
  python ttt_worker.py

The LoRA adapter is saved to ~/.quil/lora_adapters/ and can be loaded
into future Ollama calls via --adapter (when Ollama supports GGUF-LoRA).
"""

import json
import subprocess
import time
import sys
import os
import math
from pathlib import Path
from datetime import datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────────────────────────

TTT_QUEUE_PATH  = Path.home() / ".quil" / "ttt_queue.jsonl"
ADAPTER_PATH    = Path.home() / ".quil" / "lora_adapters"
TRAIN_DATA_PATH = Path.home() / ".quil" / "ttt_train"
LOG_PATH        = Path.home() / ".quil" / "ttt_worker.log"

# Minimum number of queued training pairs before triggering a training run.
# Small batches cause overfitting; 20 pairs ≈ 3-5 min training on M4.
TRAIN_BATCH_SIZE = 20

# MLX model identifier — must be the MLX-converted version of your Ollama model.
# Download with: mlx_lm.convert --hf-path Qwen/Qwen3-4B --mlx-path ~/.quil/qwen3-4b-mlx
MLX_MODEL_PATH = str(Path.home() / ".quil" / "qwen3-4b-mlx")

# LoRA training hyperparameters (tuned for M4 16GB, 4B model)
LORA_CONFIG = {
    "--num-layers": "4",       # Only last 4 transformer layers — ~3 min on M4
    "--iters": "60",           # Short run to avoid catastrophic forgetting
    "--batch-size": "1",       # 16GB constraint: batch=1 only
    "--learning-rate": "5e-6", # Low LR to suppress destructive gradient updates
    "--lora-rank": "8",        # Rank-8 LoRA: balance of capacity vs memory
}

POLL_INTERVAL_SECONDS = 300   # Check queue every 5 minutes


# ─── LOGGING ──────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ─── QUEUE READER ─────────────────────────────────────────────────────────────

def read_queue() -> list[dict]:
    if not TTT_QUEUE_PATH.exists():
        return []
    try:
        lines = TTT_QUEUE_PATH.read_text().strip().splitlines()
        return [json.loads(l) for l in lines if l.strip()]
    except Exception as e:
        log(f"Queue read error: {e}")
        return []


def clear_queue():
    try:
        TTT_QUEUE_PATH.unlink(missing_ok=True)
        log("Queue cleared after training.")
    except Exception as e:
        log(f"Queue clear error: {e}")


# ─── TRAINING DATA PREP ───────────────────────────────────────────────────────

def prepare_training_data(pairs: list[dict]) -> Path:
    """
    Converts queue entries to MLX-LM chat format JSONL.
    Each entry: {"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
    """
    TRAIN_DATA_PATH.mkdir(parents=True, exist_ok=True)
    train_file = TRAIN_DATA_PATH / "train.jsonl"
    valid_file = TRAIN_DATA_PATH / "valid.jsonl"

    # 90/10 split
    split = max(1, len(pairs) - len(pairs) // 10)
    train_pairs = pairs[:split]
    valid_pairs = pairs[split:] or pairs[:1]  # validation needs ≥1 entry

    def _to_chat_format(pair: dict) -> str:
        entry = {
            "messages": [
                {"role": "user",      "content": pair.get("prompt", "")},
                {"role": "assistant", "content": pair.get("completion", "")},
            ]
        }
        return json.dumps(entry)

    train_file.write_text("\n".join(_to_chat_format(p) for p in train_pairs))
    valid_file.write_text("\n".join(_to_chat_format(p) for p in valid_pairs))

    log(f"Training data prepared: {len(train_pairs)} train, {len(valid_pairs)} valid.")
    return TRAIN_DATA_PATH


# ─── MLX LORA TRAINING ────────────────────────────────────────────────────────

def check_mlx_available() -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import mlx_lm; print('ok')"],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() == "ok"
    except Exception:
        return False


def run_mlx_lora_train(data_dir: Path) -> bool:
    """
    Runs mlx_lm.lora fine-tuning. Returns True on success.
    Training on M4 16GB with 4B model, 4 layers, 60 iters ≈ 3-4 minutes.
    """
    ADAPTER_PATH.mkdir(parents=True, exist_ok=True)

    if not Path(MLX_MODEL_PATH).exists():
        log(f"⚠️  MLX model not found at {MLX_MODEL_PATH}.")
        log("   Run: python -m mlx_lm.convert --hf-path Qwen/Qwen3-4B-Instruct "
            f"--mlx-path {MLX_MODEL_PATH} -q --q-bits 4")
        return False

    cmd = [
        sys.executable, "-m", "mlx_lm.lora",
        "--model", MLX_MODEL_PATH,
        "--train",
        "--data", str(data_dir),
        "--adapter-path", str(ADAPTER_PATH),
    ]
    for flag, val in LORA_CONFIG.items():
        cmd.extend([flag, val])

    log(f"🧠 Starting LoRA training: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,   # 10 min hard timeout
        )
        if result.returncode == 0:
            log("✅ LoRA training complete. Adapter saved to ~/.quil/lora_adapters/")
            if result.stdout:
                # Log final loss line only
                for line in result.stdout.splitlines()[-5:]:
                    if line.strip():
                        log(f"   {line}")
            return True
        else:
            log(f"❌ LoRA training failed (rc={result.returncode}):")
            log(result.stderr[-800:] if result.stderr else "(no stderr)")
            return False
    except subprocess.TimeoutExpired:
        log("❌ LoRA training timed out (>10 min). Check GPU memory.")
        return False
    except Exception as e:
        log(f"❌ LoRA training exception: {e}")
        return False


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main():
    log("🧠 Quil TTT Worker started.")
    log(f"   Queue: {TTT_QUEUE_PATH}")
    log(f"   Adapter output: {ADAPTER_PATH}")
    log(f"   Batch threshold: {TRAIN_BATCH_SIZE} pairs")
    log(f"   Poll interval: {POLL_INTERVAL_SECONDS}s")

    if not check_mlx_available():
        log("⚠️  mlx-lm not installed. TTT will queue pairs but skip training.")
        log("   Install with: pip install mlx-lm")

    while True:
        try:
            pairs = read_queue()

            if len(pairs) >= TRAIN_BATCH_SIZE:
                log(f"📋 {len(pairs)} training pairs queued — triggering LoRA fine-tune.")
                data_dir = prepare_training_data(pairs)

                if check_mlx_available():
                    success = run_mlx_lora_train(data_dir)
                    if success:
                        clear_queue()
                    else:
                        log("Training failed — keeping queue for next attempt.")
                else:
                    log("mlx-lm unavailable — skipping training, keeping queue.")
            else:
                if pairs:
                    log(f"   Queue: {len(pairs)}/{TRAIN_BATCH_SIZE} pairs (waiting for batch).")

        except KeyboardInterrupt:
            log("TTT Worker stopped by user.")
            break
        except Exception as e:
            log(f"Worker loop error: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
