"""Create benchmark visualization charts."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os

# Output directory
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Color palette ──
OLD_COLOR = '#E74C3C'   # Red for old/heuristic
NEW_COLOR = '#2ECC71'   # Green for new/OSS
NEUTRAL = '#3498DB'     # Blue for neutral
BG_COLOR = '#FAFAFA'

plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams.update({
    'font.size': 11,
    'axes.titlesize': 14,
    'axes.labelsize': 12,
    'figure.facecolor': BG_COLOR,
    'axes.facecolor': 'white',
})


def chart_compression():
    """Chart 1: Context Compression comparison."""
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.suptitle('Benchmark 1: Context Compression — Heuristic vs LLMLingua', fontsize=16, fontweight='bold')

    samples = ['Backend\nFastAPI', 'Frontend\nReact', 'DevOps\nCI/CD', 'Security\nAudit', 'Architect\nDesign']
    original = [180, 208, 214, 239, 275]
    heuristic = [62, 48, 51, 35, 36]
    llmlingua = [136, 147, 145, 151, 184]

    # Chart 1a: Token counts
    ax = axes[0]
    x = np.arange(len(samples))
    w = 0.25
    ax.bar(x - w, original, w, label='Original', color=NEUTRAL, alpha=0.5)
    ax.bar(x, heuristic, w, label='Heuristic', color=OLD_COLOR)
    ax.bar(x + w, llmlingua, w, label='LLMLingua', color=NEW_COLOR)
    ax.set_ylabel('Token Count')
    ax.set_title('Tokens After Compression')
    ax.set_xticks(x)
    ax.set_xticklabels(samples, fontsize=9)
    ax.legend()

    # Chart 1b: Compression ratios (lower = more aggressive)
    ax = axes[1]
    h_ratios = [0.344, 0.231, 0.238, 0.146, 0.131]
    l_ratios = [0.756, 0.707, 0.678, 0.632, 0.669]
    ax.bar(x - 0.15, h_ratios, 0.3, label='Heuristic', color=OLD_COLOR)
    ax.bar(x + 0.15, l_ratios, 0.3, label='LLMLingua', color=NEW_COLOR)
    ax.set_ylabel('Compression Ratio (tokens kept)')
    ax.set_title('Compression Ratio\n(lower = more aggressive)')
    ax.set_xticks(x)
    ax.set_xticklabels(samples, fontsize=9)
    ax.set_ylim(0, 1)
    ax.axhline(y=0.5, color='gray', linestyle='--', alpha=0.5, label='50% target')
    ax.legend()

    # Chart 1c: Semantic preservation scores
    ax = axes[2]
    h_scores = [4, 4, 4]
    l_scores = [5, 6, 7]
    labels = ['Backend', 'Frontend', 'DevOps']
    x2 = np.arange(len(labels))
    ax.bar(x2 - 0.15, h_scores, 0.3, label='Heuristic', color=OLD_COLOR)
    ax.bar(x2 + 0.15, l_scores, 0.3, label='LLMLingua', color=NEW_COLOR)
    ax.set_ylabel('Semantic Score (1-10)')
    ax.set_title('Semantic Preservation\n(LLM-evaluated, higher = better)')
    ax.set_xticks(x2)
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 10)
    ax.axhline(y=4.0, color=OLD_COLOR, linestyle='--', alpha=0.3)
    ax.axhline(y=6.0, color=NEW_COLOR, linestyle='--', alpha=0.3)

    # Add avg annotations
    ax.annotate(f'Avg: 4.0', xy=(2.3, 4.0), fontsize=10, color=OLD_COLOR, fontweight='bold')
    ax.annotate(f'Avg: 6.0', xy=(2.3, 6.0), fontsize=10, color=NEW_COLOR, fontweight='bold')
    ax.legend()

    plt.tight_layout()
    path = os.path.join(OUT_DIR, 'chart_compression.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {path}")


def chart_memory():
    """Chart 2: Memory Search comparison."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle('Benchmark 2: Memory Search — JSON Keyword vs ChromaDB Semantic', fontsize=16, fontweight='bold')

    queries = [
        'container\nnetworking',
        'database\nconnection',
        'secure API\nendpoints',
        'deployment\npipeline',
        'real-time\nupdates',
        'infra as\ncode',
        'frontend\nbuild',
        'auth token\nmgmt',
    ]
    json_recall = [67, 50, 50, 33, 50, 0, 33, 100]
    chroma_recall = [67, 100, 100, 100, 100, 0, 67, 100]

    # Chart 2a: Per-query recall
    ax = axes[0]
    x = np.arange(len(queries))
    ax.bar(x - 0.15, json_recall, 0.3, label='JSON Keyword', color=OLD_COLOR)
    ax.bar(x + 0.15, chroma_recall, 0.3, label='ChromaDB Semantic', color=NEW_COLOR)
    ax.set_ylabel('Recall (%)')
    ax.set_title('Per-Query Recall')
    ax.set_xticks(x)
    ax.set_xticklabels(queries, fontsize=8)
    ax.set_ylim(0, 110)
    ax.legend()

    # Highlight wins
    for i in range(len(queries)):
        if chroma_recall[i] > json_recall[i]:
            ax.annotate('★', xy=(i + 0.15, chroma_recall[i] + 2), fontsize=12, ha='center', color=NEW_COLOR)

    # Chart 2b: Overall summary
    ax = axes[1]
    methods = ['JSON\nKeyword', 'ChromaDB\nSemantic']
    avgs = [47.9, 79.2]
    colors = [OLD_COLOR, NEW_COLOR]
    bars = ax.bar(methods, avgs, color=colors, width=0.5)
    ax.set_ylabel('Average Recall (%)')
    ax.set_title('Overall Recall\n(+65% improvement)')
    ax.set_ylim(0, 100)

    for bar, val in zip(bars, avgs):
        ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 2,
                f'{val:.1f}%', ha='center', va='bottom', fontweight='bold', fontsize=14)

    # Add improvement arrow
    ax.annotate('', xy=(1, 79.2), xytext=(0, 47.9),
                arrowprops=dict(arrowstyle='->', color='#2C3E50', lw=2))
    ax.text(0.5, 63, '+65%', ha='center', fontsize=16, fontweight='bold', color='#2C3E50')

    plt.tight_layout()
    path = os.path.join(OUT_DIR, 'chart_memory.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {path}")


def chart_routing():
    """Chart 3: Model Routing comparison."""
    fig, axes = plt.subplots(1, 3, figsize=(16, 6))
    fig.suptitle('Benchmark 3: Model Routing — Static vs Smart Classifier', fontsize=16, fontweight='bold')

    # Chart 3a: Routing accuracy
    ax = axes[0]
    ax.bar(['Smart\nRouter'], [86.7], color=NEW_COLOR, width=0.4)
    ax.bar(['Static\n(all strong)'], [100], color=OLD_COLOR, width=0.4, alpha=0.5)
    ax.set_ylabel('Accuracy (%)')
    ax.set_title('Routing Accuracy\n(correct model selection)')
    ax.set_ylim(0, 110)
    ax.text(0, 89, '86.7%', ha='center', fontweight='bold', fontsize=14, color=NEW_COLOR)
    ax.text(1, 103, '100%*', ha='center', fontweight='bold', fontsize=14, color=OLD_COLOR)
    ax.text(0.5, -15, '*Static always picks strong = "correct" but wasteful',
            ha='center', fontsize=9, style='italic', color='gray', transform=ax.get_xaxis_transform())

    # Chart 3b: Model distribution
    ax = axes[1]
    labels = ['Strong\n(Sonnet)', 'Weak\n(Haiku)']
    static_dist = [100, 0]
    smart_dist = [40, 60]
    x = np.arange(len(labels))
    ax.bar(x - 0.15, static_dist, 0.3, label='Static (all strong)', color=OLD_COLOR, alpha=0.5)
    ax.bar(x + 0.15, smart_dist, 0.3, label='Smart Router', color=NEW_COLOR)
    ax.set_ylabel('% of Tasks')
    ax.set_title('Model Distribution')
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 110)
    ax.legend()

    # Chart 3c: Cost comparison
    ax = axes[2]
    costs = ['Static\n(all Sonnet)', 'Smart\nRouter']
    values = [2.25, 1.08]
    colors_c = [OLD_COLOR, NEW_COLOR]
    bars = ax.bar(costs, values, color=colors_c, width=0.4)
    ax.set_ylabel('Estimated Cost ($)')
    ax.set_title('Cost per 15 Tasks\n(52% savings)')
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 0.05,
                f'${val:.2f}', ha='center', va='bottom', fontweight='bold', fontsize=14)

    # Savings annotation
    ax.annotate('', xy=(1, 1.08), xytext=(0, 2.25),
                arrowprops=dict(arrowstyle='->', color='#2C3E50', lw=2))
    ax.text(0.5, 1.7, '-52%', ha='center', fontsize=16, fontweight='bold', color='#2C3E50')

    plt.tight_layout()
    path = os.path.join(OUT_DIR, 'chart_routing.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {path}")


def chart_summary():
    """Summary chart: Overall improvement across all 3 benchmarks."""
    fig, ax = plt.subplots(figsize=(10, 6))
    fig.suptitle('HiveMind OSS Outsourcing — Overall Benchmark Results', fontsize=16, fontweight='bold')

    categories = ['Semantic\nPreservation', 'Memory\nRecall', 'Cost\nSavings']
    old_vals = [40, 47.9, 0]  # heuristic score 4/10 = 40%, JSON recall 47.9%, no savings
    new_vals = [60, 79.2, 52]  # LLMLingua 6/10 = 60%, ChromaDB 79.2%, 52% savings

    x = np.arange(len(categories))
    bars_old = ax.bar(x - 0.17, old_vals, 0.34, label='Current (heuristic/JSON/static)', color=OLD_COLOR)
    bars_new = ax.bar(x + 0.17, new_vals, 0.34, label='OSS (LLMLingua/ChromaDB/RouteLLM)', color=NEW_COLOR)

    ax.set_ylabel('Score / Percentage (%)')
    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=12)
    ax.set_ylim(0, 100)
    ax.legend(fontsize=11, loc='upper left')

    # Add value labels
    for bar, val in zip(bars_old, old_vals):
        ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 1.5,
                f'{val:.0f}%', ha='center', va='bottom', fontweight='bold', fontsize=12, color=OLD_COLOR)
    for bar, val in zip(bars_new, new_vals):
        ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 1.5,
                f'{val:.0f}%', ha='center', va='bottom', fontweight='bold', fontsize=12, color=NEW_COLOR)

    # Improvement annotations
    improvements = ['+50%', '+65%', '+52%']
    for i, imp in enumerate(improvements):
        ax.annotate(imp, xy=(i, max(old_vals[i], new_vals[i]) + 8),
                    fontsize=14, fontweight='bold', ha='center', color='#2C3E50',
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='#F0F0F0', edgecolor='#2C3E50'))

    plt.tight_layout()
    path = os.path.join(OUT_DIR, 'chart_summary.png')
    plt.savefig(path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {path}")


if __name__ == '__main__':
    print("[*] Creating benchmark charts...")
    chart_compression()
    chart_memory()
    chart_routing()
    chart_summary()
    print("[✓] All charts created successfully")
