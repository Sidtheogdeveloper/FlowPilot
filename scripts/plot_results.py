"""Plot recorded native benchmarks; does not run or alter any workflow."""
from pathlib import Path
import csv
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parents[1]
policies = ['greedy', 'heft', 'bounded']
labels = ['Greedy', 'HEFT adaptation', 'Bounded search']
fig, axes = plt.subplots(2, 2, figsize=(10, 6), layout='constrained')
for row, (folder, title) in enumerate([('single', 'Single workflow'), ('concurrent_3', 'Three concurrent workflows')]):
    with (root / 'evidence' / folder / 'benchmark.csv').open(newline='') as stream:
        records = list(csv.DictReader(stream))
    for col, (field, ylabel) in enumerate([('group_wall_seconds', 'Group wall time (seconds)'), ('scheduling_ms', 'Scheduling overhead (milliseconds)')]):
        ax = axes[row, col]
        for x, policy in enumerate(policies):
            values = [float(r[field]) for r in records if r['policy'] == policy]
            mean = statistics.mean(values)
            ax.errorbar(x, mean, yerr=[[mean-min(values)], [max(values)-mean]], fmt='s', color='black', capsize=5, markersize=5)
            ax.scatter([x-.08, x, x+.08], values, facecolors='white', edgecolors='black', s=25, zorder=3)
        ax.set_xticks(range(3), labels)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.set_ylim(0, max(float(r[field]) for r in records) * 1.15)
        ax.grid(axis='y', color='0.88')
        ax.spines[['top', 'right']].set_visible(False)
fig.suptitle('Measured Phase 2 scheduling comparison on one physical host')
fig.text(.5, -.025, 'Three recorded repetitions per policy. Squares: mean; circles: runs; whiskers: min–max. Warm-up excluded.', ha='center', fontsize=9)
for extension in ['png', 'pdf']:
    fig.savefig(root / 'evidence' / ('scheduling_comparison.' + extension), dpi=180, bbox_inches='tight')
print('Created scheduling_comparison.png and .pdf from existing recorded CSV files')
