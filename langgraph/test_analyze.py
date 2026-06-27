"""analyze 节点快速验证 — 不调 LLM，纯逻辑测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nodes.analyze import analyze_node

cases = [
    # (消息, 期望intent, 期望province)
    ('浙江考生位次5000想学计算机', 'recommend', '浙江'),
    ('你好', 'collect', ''),
    ('浙江大学和复旦大学怎么选', 'compare', ''),
    ('浙江填几个志愿', 'policy', '浙江'),
    ('计算机专业怎么样', 'explain', ''),
    ('我是湖北的', 'recommend', '湖北'),   # 有省份→recommend，缺位次→needs_ask追问
    ('位次8000', 'collect', ''),            # 无省份，只有位次→collect追问省份
]

for msg, exp_intent, exp_prov in cases:
    state = {'user_message': msg, 'profile': {}}
    r = analyze_node(state)
    ok_intent = r['intent'] == exp_intent
    ok_prov = not exp_prov or r['profile'].get('province') == exp_prov
    mark = 'pass' if (ok_intent and ok_prov) else 'FAIL'
    prov = r['profile'].get('province', '')
    print(f"[{mark:>4}] intent={r['intent']:>8}  prov={prov:<4}  needs_ask={r['needs_ask']:<5}  | {msg}")
