"""比较两组 Agent JSON 轨迹，输出默认架构切换门禁结果。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 支持从仓库根目录直接运行，无需先把项目安装为 editable package。
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from experiment_guardian.application.agent_evaluation import (  # noqa: E402
    AgentEvaluationObservation,
    compare_architectures,
    evaluate_observations,
)


def _load(path: Path) -> list[AgentEvaluationObservation]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} 必须包含 observation 数组")
    return [AgentEvaluationObservation.model_validate(item) for item in raw]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    comparison = compare_architectures(
        evaluate_observations(_load(args.baseline)),
        evaluate_observations(_load(args.candidate)),
    )
    rendered = json.dumps(comparison.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
