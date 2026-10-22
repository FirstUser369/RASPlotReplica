export function finiteElevation(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function profileSegments(points: Array<[number, number | null | undefined]>): Array<Array<[number, number]>> {
  const runs: Array<Array<[number, number]>> = [];
  let run: Array<[number, number]> = [];
  for (const [x, y] of points) {
    if (!Number.isFinite(x) || !finiteElevation(y)) {
      if (run.length) runs.push(run);
      run = [];
    } else run.push([x, y]);
  }
  if (run.length) runs.push(run);
  return runs;
}
