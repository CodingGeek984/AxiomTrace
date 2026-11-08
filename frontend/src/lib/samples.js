"use client";

/**
 * Demo sources that can be dropped onto the canvas with one click.
 *
 * Each sample is deliberately chosen to exercise a different part of the
 * product: control flow, async execution, a node that is robust under
 * mutation, and one that is deliberately fragile so the stress-test panel
 * has something to report.
 */

export const SAMPLES = [
  {
    id: "pipeline",
    label: "Etl pipeline",
    hint: "loops, conditions and function chaining",
    fileName: "etl_pipeline.py",
    code: `def load(records):
    """Normalise raw records before they reach the transformer."""
    return [record for record in records if record]


def transform(records):
    """Apply the mapping rules and drop anything that failed."""
    output = []
    for record in records:
        if not record:
            continue
        output.append({"payload": record, "score": len(str(record))})
    return output


def persist(records):
    """Write the transformed batch and return a summary."""
    if not records:
        return {"written": 0, "status": "empty"}
    return {"written": len(records), "status": "ok"}


def main(batch):
    records = load(batch)
    transformed = transform(records)
    return persist(transformed)
`,
  },
  {
    id: "retry",
    label: "Retry policy",
    hint: "bounded loop with backoff and error handling",
    fileName: "retry_policy.py",
    code: `def main(attempts):
    """Retry a flaky operation with a bounded loop."""
    delays = [1, 2, 4, 8]
    total = 0
    for attempt in range(attempts):
        if attempt >= len(delays):
            break
        total += delays[attempt]
    return {"attempts": attempts, "backoff": total}
`,
  },
  {
    id: "async-fetch",
    label: "Async fetcher",
    hint: "coroutines and awaited results",
    fileName: "async_fetcher.py",
    code: `async def fetch(url, timeout=5):
    """Resolve a single resource, never hanging the scheduler."""
    if not url:
        return {"url": None, "body": None}
    return {"url": url, "body": "..."}


async def main(urls):
    results = []
    for url in urls:
        results.append(await fetch(url))
    return {"count": len(results), "results": results}
`,
  },
  {
    id: "fragile",
    label: "Fragile node",
    hint: "crashes on the edge-case corpus - see the mutation report",
    fileName: "fragile_node.py",
    code: `def main(value):
    """Deliberately brittle: no guards, no types, no fallback."""
    return value + 1
`,
  },
  {
    id: "hardened",
    label: "Hardened node",
    hint: "same logic, fully guarded - mutation verdict stays stable",
    fileName: "hardened_node.py",
    code: `def main(value):
    """Defensive twin of the fragile node."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        return value + 1
    except (TypeError, ValueError, OverflowError):
        return 0
`,
  },
  {
    id: "js-fetch",
    label: "Js branch",
    hint: "JavaScript/TypeScript branch handling",
    fileName: "js_branch.js",
    code: `function route(request) {
  if (!request) {
    return { status: 400 };
  } else if (request.method === "GET") {
    return { status: 200 };
  }
  return { status: 405 };
}
`,
  },
];

export const SAMPLE_IDS = SAMPLES.map((sample) => sample.id);

/**
 * Look a sample up by id.
 *
 * @param {string} id
 * @returns {{ id: string, label: string, hint: string, fileName: string, code: string } | null}
 */
export function findSample(id) {
  return SAMPLES.find((sample) => sample.id === id) || null;
}
