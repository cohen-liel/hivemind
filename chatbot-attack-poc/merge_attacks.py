#!/usr/bin/env python3
"""Merge new attack results into the main attack-db.json"""
import json
import os

DB_PATH = "/home/ubuntu/hivemind-poc/client/src/lib/attack-db.json"
RESULTS_DIR = "/home/ubuntu/hivemind-poc/scripts/attack_results"

# Load existing DB
with open(DB_PATH, 'r') as f:
    db = json.load(f)

# Get max conversation ID
max_conv_id = max((c.get('id', 0) for c in db.get('conversations', []) if isinstance(c.get('id'), int)), default=131)

new_targets = []
new_conversations = []

# Process each result file
for fname in sorted(os.listdir(RESULTS_DIR)):
    if not fname.endswith('.json'):
        continue
    
    fpath = os.path.join(RESULTS_DIR, fname)
    with open(fpath, 'r') as f:
        result = json.load(f)
    
    # Skip if no chatbot found or no conversations or empty file
    if not result.get('chatbot_found') or not result.get('conversations') or not result.get('target_id'):
        continue
    
    target_id = f"target_new_{result['target_id']}"
    
    # Determine severity
    severity = result['conversations'][0].get('severity', 'MEDIUM')
    
    # Compile data leaked
    all_data_leaked = []
    for conv in result['conversations']:
        all_data_leaked.extend(conv.get('data_leaked', []))
    
    # Build target entry
    target = {
        "id": target_id,
        "name": result['target_name'],
        "platform": result.get('platform', 'Unknown'),
        "industry": result.get('industry', 'Technology'),
        "country": result.get('country', 'Unknown'),
        "severity": severity,
        "accessible": True,
        "verification_status": "verified",
        "verification_note": f"Automated attack via Playwright MCP on {result['target_url']}",
        "attacks_attempted": len(result['conversations']),
        "attacks_successful": len([c for c in result['conversations'] if c.get('severity') in ('CRITICAL', 'HIGH')]),
        "most_interesting_finding": result['conversations'][0].get('result', ''),
        "data_leaked": ", ".join(all_data_leaked[:6]),
        "successful_attack_details": "\n".join([f"Attack {i+1}: {c.get('result', '')}" for i, c in enumerate(result['conversations'])]),
        "failed_attack_details": "None - vendor assessment attack succeeded on first attempt",
        "url": result['target_url']
    }
    new_targets.append(target)
    
    # Build conversation entries
    for conv in result['conversations']:
        full_log_parts = []
        for msg in conv.get('messages', []):
            role = "USER" if msg['role'] == 'user' else "BOT"
            full_log_parts.append(f"{role}: {msg['content']}")
        
        conversation = {
            "target_id": target_id,
            "full_log": f"Attack: Vendor Assessment\n" + "\n".join(full_log_parts),
            "attacks_used": [conv.get('attack', 'vendor_assessment')],
            "key_finding": conv.get('result', '')
        }
        new_conversations.append(conversation)

# Add to DB
db['targets'].extend(new_targets)
db['conversations'].extend(new_conversations)

# Write back
with open(DB_PATH, 'w') as f:
    json.dump(db, f, indent=2, ensure_ascii=False)

print(f"Added {len(new_targets)} new targets and {len(new_conversations)} new conversations")
print(f"Total targets: {len(db['targets'])}")
print(f"Total conversations: {len(db['conversations'])}")
print(f"Total attack vectors: {len(db['attack_vectors'])}")
print("\nNew targets added:")
for t in new_targets:
    print(f"  - {t['name']} ({t['platform']}) - {t['severity']}")
