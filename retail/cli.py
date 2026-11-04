import argparse
import asyncio
import json
from .learning import train, evaluate, compare_forecasts


def main():
    p = argparse.ArgumentParser(
        description="ShelfIQ software workers and reproducible experiments"
    )
    sub = p.add_subparsers(dest="command", required=True)
    w = sub.add_parser("worker")
    w.add_argument("role", choices=["simulator", "forecaster", "pricing", "device"])
    t = sub.add_parser("train")
    t.add_argument("--algorithm", choices=["dqn", "ddpg"], default="dqn")
    t.add_argument("--steps", type=int, default=10000)
    t.add_argument("--seed", type=int, default=42)
    t.add_argument("--output", default="artifacts/dqn.zip")
    e = sub.add_parser("evaluate")
    e.add_argument("--dqn")
    e.add_argument("--ddpg")
    e.add_argument("--seeds", default="101,102,103,104,105")
    e.add_argument("--days", type=int, default=30)
    e.add_argument("--output", default="artifacts/evaluation")
    f = sub.add_parser("forecast")
    f.add_argument("--methods", default="mean,seasonal,lstm")
    f.add_argument("--seed", type=int, default=42)
    f.add_argument("--output", default="artifacts/evaluation")
    args = p.parse_args()
    if args.command == "worker":
        from .workers import run

        asyncio.run(run(args.role))
        return
    if args.command == "train":
        result = train(args.algorithm, args.steps, args.seed, args.output)
    elif args.command == "evaluate":
        result = evaluate(
            {k: getattr(args, k) for k in ["dqn", "ddpg"] if getattr(args, k)},
            [int(x) for x in args.seeds.split(",")],
            args.days,
            args.output,
        )
    else:
        result = compare_forecasts(args.methods.split(","), args.seed, args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
