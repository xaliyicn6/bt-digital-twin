"""把原始 LAS/LAZ 转成 Potree 八叉树，供前端 Potree 查看器直接加载。

为什么需要这一步：Potree 1.8 查看器读不了 3D Tiles 的 .pnts，它只认 Potree
八叉树目录（metadata.json + octree.bin + hierarchy.bin）。因此桥隧近景视图切到
Potree 后，数据链路改为「原始 LAS → PotreeConverter → 静态目录 → 前端」。

转换器版本：
- 必须用 PotreeConverter 2.1.x（Windows x64），输出 Potree 2.0 格式；
  Potree 1.8.2 viewer 能直接读 2.0 数据（octree.bin / hierarchy.bin）。
- 1.7 版在本机必崩（0xC0000005 访问冲突，连 200 点合成 LAS 也崩），不要用。
- 转换器是外部二进制，不入库；默认放在 backend/var/tools/（已被 .gitignore 覆盖）。
  获取方式见 docs/adr/ADR-0002-potree-micro-3d-viewer.md。

约定：
- 产物落在 frontend/public/tiles/potree/<dataset>/，该目录已被 .gitignore 覆盖，
  真实业主点云及其派生产物不得提交进仓库。
- 脚本幂等：metadata.json 已存在且未指定 --force 时直接跳过。

用法：
    python backend/scripts/build_potree.py --dataset tunnel \
        --input backend/tests/lunxiao-L.las \
        --aabb "226936.188 103223.594 -45.928 228155.516 104149.18 -21.059"
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # noqa: S404 - 外部转换器必须走子进程
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONVERTER = (
    REPO_ROOT
    / "backend"
    / "var"
    / "tools"
    / "pc215"
    / "PotreeConverter_2.1.5_x64_windows"
    / "PotreeConverter.exe"
)
DEFAULT_OUT_ROOT = REPO_ROOT / "frontend" / "public" / "tiles" / "potree"


def build_command(
    converter: Path,
    source: Path,
    outdir: Path,
    encoding: str = "UNCOMPRESSED",
    aabb: str | None = None,
) -> list[str]:
    """拼装 PotreeConverter 2.x 命令行；抽成纯函数便于单测。

    2.x 取消了 1.7 的 `-s/--spacing`、`-l/--levels`、`--store-size`，点预算改由
    内部索引策略控制；`--attributes` 传空格式会被忽略，因此默认不传（输出 position
    + intensity）。`--aabb` 用于源文件头包围盒损坏时显式指定范围。
    """
    cmd = [
        str(converter),
        str(source),
        "-o",
        str(outdir),
        "--encoding",
        encoding,
    ]
    if aabb:
        cmd += ["--aabb", aabb]
    return cmd


def dir_size_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def summarize(outdir: Path) -> dict[str, object]:
    """读取转换产物，返回供日志/前端校验的结构摘要。"""
    meta_path = outdir / "metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"转换产物缺少 metadata.json：{meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    bins = sorted(outdir.rglob("*.bin"))
    return {
        "version": meta.get("version"),
        "points": meta.get("points"),
        "octreeDir": meta.get("octreeDir"),
        "spacing": meta.get("spacing"),
        "scale": meta.get("scale"),
        "boundingBox": meta.get("boundingBox"),
        "nodeFiles": len(bins),
        "bytes": dir_size_bytes(outdir),
        "hierarchy": [p.name for p in outdir.rglob("hierarchy*")],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LAS/LAZ → Potree 2.0 八叉树")
    parser.add_argument("--dataset", required=True, help="数据集代号，如 tunnel / bridge")
    parser.add_argument("--input", required=True, help="原始 .las / .laz 路径")
    parser.add_argument(
        "--aabb",
        default=None,
        help='源包围盒，格式 "minX minY minZ maxX maxY maxZ"；LAS 头损坏时必须显式指定',
    )
    parser.add_argument(
        "--encoding",
        default="UNCOMPRESSED",
        choices=["UNCOMPRESSED", "BROTLI"],
        help="节点数据编码；本地点云演示用 UNCOMPRESSED 便于排查，线上可换 BROTLI 省带宽",
    )
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT), help="产物根目录")
    parser.add_argument(
        "--converter", default=str(DEFAULT_CONVERTER), help="PotreeConverter 2.1.x 可执行文件"
    )
    parser.add_argument("--force", action="store_true", help="已有产物时删除后重转")
    args = parser.parse_args(argv)

    source = Path(args.input).resolve()
    converter = Path(args.converter).resolve()
    outdir = Path(args.out_root).resolve() / args.dataset

    if not source.exists():
        print(f"[potree] 源文件不存在：{source}", file=sys.stderr)
        return 2
    if not converter.exists():
        print(
            f"[potree] 未找到 PotreeConverter：{converter}\n"
            "         请按 ADR-0002 下载 PotreeConverter 2.1.5（Windows x64）解压到 "
            "backend/var/tools/pc215/，或用 --converter 指定路径。",
            file=sys.stderr,
        )
        return 3
    if (outdir / "metadata.json").exists() and not args.force:
        print(f"[potree] 已存在产物，跳过（--force 可重转）：{outdir}")
        print(json.dumps(summarize(outdir), ensure_ascii=False, indent=2))
        return 0
    if outdir.exists() and args.force:
        shutil.rmtree(outdir)

    outdir.mkdir(parents=True, exist_ok=True)
    cmd = build_command(converter, source, outdir, args.encoding, args.aabb)
    print("[potree] " + " ".join(cmd))
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(converter.parent), check=False)  # noqa: S603
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        print(f"[potree] 转换失败，退出码 {proc.returncode}", file=sys.stderr)
        return 4

    info = summarize(outdir)
    print(f"[potree] 完成，用时 {elapsed:.1f}s")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print(f"[potree] 访问路径：/tiles/potree/{args.dataset}/metadata.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
