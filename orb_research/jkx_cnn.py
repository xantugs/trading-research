"""The baseline CNN of Jiang, Kelly & Xiu (2023) and its training (docs/JKX_REPLICATION_PLAN.md).

Each building block is a 5x3 convolution, batch normalisation, leaky ReLU and 2x1 max-pooling.
5/20/60-day images use 2/3/4 blocks with 64, 128, 256, 512 filters. Only the first convolution
has a vertical stride and dilation: (1, 1), (3, 2) and (3, 3). The flattened output feeds a 50%
dropout and a fully connected layer with two outputs (down, up). With batch-norm parameters
counted, the models have exactly the paper's 155,138 / 708,866 / 2,952,962 parameters.

Training follows the paper: Xavier initialisation, Adam with learning rate 1e-5, batch 128,
cross-entropy, a random 70/30 train/validation split, early stopping after two epochs without
a lower validation loss, pixels standardised with the training images' mean and std, and an
ensemble of five independently trained models.
"""
from __future__ import annotations

import time

import numpy as np
import torch
from torch import nn

from .jkx_images import image_shape

BLOCKS = {5: 2, 20: 3, 60: 4}
FIRST_CONV = {5: {"stride": (1, 1), "dilation": (1, 1), "padding": (2, 1)},
              20: {"stride": (3, 1), "dilation": (2, 1), "padding": (12, 1)},
              60: {"stride": (3, 1), "dilation": (3, 1), "padding": (12, 1)}}
PAPER_PARAMS = {5: 155_138, 20: 708_866, 60: 2_952_962}
PAPER_FC_INPUTS = {5: 15_360, 20: 46_080, 60: 184_320}


class ChartCNN(nn.Module):
    def __init__(self, days: int, shape: tuple[int, int] | None = None):
        """`days` picks the JKX architecture (5/20/60); `shape` overrides the input image size
        (intraday charts use the 20-day architecture on wider images)."""
        super().__init__()
        self.days = days
        layers, channels = [], 1
        for k in range(BLOCKS[days]):
            out = 64 * 2 ** k
            conv = FIRST_CONV[days] if k == 0 else {"stride": (1, 1), "dilation": (1, 1), "padding": (2, 1)}
            layers += [nn.Conv2d(channels, out, (5, 3), **conv), nn.BatchNorm2d(out), nn.LeakyReLU(0.01),
                       nn.MaxPool2d((2, 1), stride=(2, 1))]
            channels = out
        self.features = nn.Sequential(*layers)
        with torch.no_grad():
            self.fc_inputs = int(self.features(torch.zeros(1, 1, *(shape or image_shape(days)))).numel())
        self.head = nn.Sequential(nn.Flatten(), nn.Dropout(0.5), nn.Linear(self.fc_inputs, 2))
        for m in self.modules():
            if isinstance(m, (nn.Conv2d, nn.Linear)):
                nn.init.xavier_uniform_(m.weight)
                nn.init.zeros_(m.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.features(x))


def n_params(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


_SHIFTS = torch.arange(7, -1, -1, dtype=torch.uint8)


def unpack_batch(packed: torch.Tensor, days: int, mean: float, std: float,
                 shape: tuple[int, int] | None = None) -> torch.Tensor:
    """Packed uint8 rows (np.packbits order) -> standardised float images (B, 1, H, W) on the
    same device. Unpacking per batch keeps millions of images in memory at 1 bit per pixel."""
    h, w = shape or image_shape(days)
    bits = (packed.unsqueeze(-1) >> _SHIFTS.to(packed.device)) & 1
    pixels = bits.reshape(len(packed), -1)[:, : h * w].to(torch.float32) * 255.0
    return ((pixels - mean) / std).reshape(-1, 1, h, w)


def pixel_stats(packed: np.ndarray) -> tuple[float, float]:
    """Mean and std of the 0/255 pixels of packed images."""
    white = np.unpackbits(packed, axis=1).mean()        # padding bits are zero; sizes are multiples of 8
    return 255.0 * white, 255.0 * float(np.sqrt(white * (1 - white)))


def _batches(n: int, size: int, rng: np.random.Generator | None):
    order = rng.permutation(n) if rng is not None else np.arange(n)
    for i in range(0, n, size):
        yield order[i:i + size]


def train_one(packed: np.ndarray, labels: np.ndarray, days: int, seed: int, device: str = "cpu",
              lr: float = 1e-5, batch: int = 128, max_epochs: int = 100, patience: int = 2,
              valid_frac: float = 0.3, data_on_gpu: bool = True, log=print,
              shape: tuple[int, int] | None = None, valid_idx: np.ndarray | None = None) -> dict:
    """Train one model. Returns {"state", "mean", "std", "epochs", "best_valid_loss", "history"}.

    `data_on_gpu=False` keeps the packed images in system memory and moves one batch at a time,
    for a GPU shared with other jobs (results are identical; only speed changes)."""
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    n = len(labels)
    if valid_idx is None:               # JKX: a random 70/30 split
        order = rng.permutation(n)
        cut = int(round(n * (1 - valid_frac)))
        tr, va = order[:cut], order[cut:]
    else:                               # a fixed (e.g. later-year) validation set
        va = np.asarray(valid_idx)
        tr = np.setdiff1d(np.arange(n), va)
    mean, std = pixel_stats(packed[tr])
    store = device if data_on_gpu else "cpu"
    x = torch.from_numpy(packed).to(store)
    y = torch.from_numpy(labels.astype(np.int64)).to(store)
    tr_t, va_t = torch.from_numpy(tr).to(store), torch.from_numpy(va).to(store)

    def batch_of(rows: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        return x[rows].to(device, non_blocking=True), y[rows].to(device, non_blocking=True)

    model = ChartCNN(days, shape).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.CrossEntropyLoss()
    best, best_state, bad, history = np.inf, None, 0, []
    for epoch in range(max_epochs):
        start = time.time()
        model.train()
        total = 0.0
        for idx in _batches(len(tr), batch, rng):
            xb, yb = batch_of(tr_t[torch.from_numpy(idx).to(store)])
            opt.zero_grad(set_to_none=True)
            loss = loss_fn(model(unpack_batch(xb, days, mean, std, shape)), yb)
            loss.backward()
            opt.step()
            total += loss.item() * len(yb)
        model.eval()
        vloss, correct = 0.0, 0
        with torch.no_grad():
            for idx in _batches(len(va), 1024, None):   # memory only: the mean loss is the same
                xb, yb = batch_of(va_t[torch.from_numpy(idx).to(store)])
                logits = model(unpack_batch(xb, days, mean, std, shape))
                vloss += loss_fn(logits, yb).item() * len(yb)
                correct += (logits.argmax(1) == yb).sum().item()
        vloss /= len(va)
        history.append({"epoch": epoch + 1, "train_loss": total / len(tr), "valid_loss": vloss,
                        "valid_acc": correct / len(va), "seconds": time.time() - start})
        log(f"  seed {seed} epoch {epoch + 1}: train {total / len(tr):.5f} valid {vloss:.5f} "
            f"acc {correct / len(va):.4f} ({time.time() - start:.0f}s)")
        if vloss < best:
            best, bad = vloss, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
    return {"state": best_state, "mean": mean, "std": std, "shape": shape, "epochs": len(history),
            "best_valid_loss": best, "history": history}


def predict(fitted: dict, packed: np.ndarray, days: int, device: str = "cpu", batch: int = 4096) -> np.ndarray:
    """Probability of an up move for each packed image."""
    shape = fitted.get("shape")
    model = ChartCNN(days, shape).to(device)
    model.load_state_dict(fitted["state"])
    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(packed), batch):
            x = torch.from_numpy(packed[i:i + batch]).to(device)
            out.append(torch.softmax(model(unpack_batch(x, days, fitted["mean"], fitted["std"], shape)), 1)[:, 1].cpu())
    return torch.cat(out).numpy() if out else np.zeros(0)
