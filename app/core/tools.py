"""健身教练智能体 - 工具集"""

from langchain_core.tools import tool
import json


@tool
def calculate_bmi(height_cm: float, weight_kg: float) -> str:
    """计算BMI指数并评估体重状况。

    Args:
        height_cm: 身高（厘米）
        weight_kg: 体重（公斤）
    """
    height_m = height_cm / 100
    bmi = weight_kg / (height_m ** 2)

    if bmi < 18.5:
        category = "偏瘦"
        advice = "建议适当增加热量摄入，配合力量训练增肌"
    elif bmi < 24:
        category = "正常"
        advice = "体重在健康范围内，建议保持均衡饮食和规律运动"
    elif bmi < 28:
        category = "偏胖"
        advice = "建议控制饮食热量，增加有氧运动频率"
    else:
        category = "肥胖"
        advice = "建议在专业指导下进行减脂，控制饮食并增加运动量"

    return json.dumps({
        "bmi": round(bmi, 1),
        "category": category,
        "advice": advice
    }, ensure_ascii=False)


@tool
def calculate_calories(
    gender: str,
    age: int,
    height_cm: float,
    weight_kg: float,
    activity_level: str,
    goal: str
) -> str:
    """计算每日所需热量（TDEE）及目标热量。

    Args:
        gender: 性别（male/female）
        age: 年龄
        height_cm: 身高（厘米）
        weight_kg: 体重（公斤）
        activity_level: 活动水平（sedentary/light/moderate/active/very_active）
        goal: 目标（lose_weight/maintain/gain_muscle）
    """
    # Mifflin-St Jeor 公式计算基础代谢率 (BMR)
    if gender.lower() == "male":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161

    # 活动系数
    activity_multipliers = {
        "sedentary": 1.2,       # 久坐不动
        "light": 1.375,         # 轻度活动（每周1-3次）
        "moderate": 1.55,       # 中度活动（每周3-5次）
        "active": 1.725,        # 高度活动（每周6-7次）
        "very_active": 1.9      # 极高活动（体力劳动/专业运动员）
    }

    multiplier = activity_multipliers.get(activity_level, 1.55)
    tdee = bmr * multiplier

    # 根据目标调整热量
    if goal == "lose_weight":
        target_calories = tdee - 500  # 每周减约0.5kg
        goal_desc = "减脂"
    elif goal == "gain_muscle":
        target_calories = tdee + 300  # 适度盈余
        goal_desc = "增肌"
    else:
        target_calories = tdee
        goal_desc = "维持体重"

    return json.dumps({
        "bmr": round(bmr),
        "tdee": round(tdee),
        "target_calories": round(target_calories),
        "goal": goal_desc,
        "macros": {
            "protein_g": round(target_calories * 0.3 / 4),   # 蛋白质 30%
            "carbs_g": round(target_calories * 0.45 / 4),    # 碳水 45%
            "fat_g": round(target_calories * 0.25 / 9)       # 脂肪 25%
        }
    }, ensure_ascii=False)


@tool
def generate_diet_plan(
    target_calories: int,
    goal: str,
    dietary_restrictions: str = "none"
) -> str:
    """根据热量目标和健身目标生成每日饮食计划。

    Args:
        target_calories: 每日目标热量（千卡）
        goal: 健身目标（lose_weight/maintain/gain_muscle）
        dietary_restrictions: 饮食限制（如 vegetarian/vegan/none）
    """
    # 根据热量范围生成饮食建议
    if target_calories < 1500:
        portion = "小份"
    elif target_calories < 2000:
        portion = "中等份量"
    else:
        portion = "大份"

    diet_plan = {
        "target_calories": target_calories,
        "goal": goal,
        "meals": [
            {
                "meal": "早餐",
                "time": "7:00-8:00",
                "options": [
                    "全麦面包2片 + 鸡蛋2个 + 牛奶250ml + 少量坚果",
                    "燕麦粥50g + 蛋白粉1勺 + 蓝莓50g + 香蕉半根",
                    "希腊酸奶200g + 燕麦30g + 蜂蜜适量 + 水果"
                ],
                "calories": round(target_calories * 0.25)
            },
            {
                "meal": "午餐",
                "time": "12:00-13:00",
                "options": [
                    "糙米饭150g + 鸡胸肉150g + 西兰花200g + 橄榄油",
                    "全麦面条200g + 牛肉100g + 蔬菜沙拉 + 少量酱汁",
                    "紫薯200g + 三文鱼150g + 芦笋200g + 柠檬汁"
                ],
                "calories": round(target_calories * 0.35)
            },
            {
                "meal": "加餐",
                "time": "15:00-16:00",
                "options": [
                    "苹果1个 + 坚果一小把（约15g）",
                    "蛋白棒1根",
                    "希腊酸奶150g + 少量蜂蜜"
                ],
                "calories": round(target_calories * 0.1)
            },
            {
                "meal": "晚餐",
                "time": "18:00-19:00",
                "options": [
                    "杂粮饭100g + 清蒸鱼150g + 炒时蔬200g",
                    "鸡胸肉沙拉（鸡胸肉150g + 生菜 + 番茄 + 黄瓜）",
                    "豆腐200g + 虾仁100g + 青菜200g + 少量米饭"
                ],
                "calories": round(target_calories * 0.3)
            }
        ],
        "tips": []
    }

    # 根据目标添加建议
    if goal == "lose_weight":
        diet_plan["tips"] = [
            "多喝水，每天至少2L",
            "减少精制碳水（白米、白面包）的摄入",
            "增加蔬菜和蛋白质的比例，增强饱腹感",
            "避免含糖饮料和高热量零食",
            "晚餐尽量在19:00前完成"
        ]
    elif goal == "gain_muscle":
        diet_plan["tips"] = [
            "训练后30分钟内补充蛋白质（蛋白粉或鸡胸肉）",
            "每餐都要有优质蛋白质来源",
            "适当增加碳水摄入，为训练提供能量",
            "睡前可补充酪蛋白或牛奶",
            "每天饮水3L以上"
        ]
    else:
        diet_plan["tips"] = [
            "保持三餐规律，营养均衡",
            "每天摄入足够的蛋白质（每公斤体重1.6-2g）",
            "多吃全谷物、蔬菜和水果",
            "适量摄入健康脂肪（坚果、鱼油、橄榄油）"
        ]

    return json.dumps(diet_plan, ensure_ascii=False, indent=2)


@tool
def generate_workout_plan(
    fitness_level: str,
    goal: str,
    days_per_week: int,
    equipment: str = "gym"
) -> str:
    """根据健身水平和目标生成周训练计划。

    Args:
        fitness_level: 健身水平（beginner/intermediate/advanced）
        goal: 训练目标（lose_weight/gain_muscle/improve_fitness）
        days_per_week: 每周训练天数（3-6天）
        equipment: 可用器材（gym/home/bodyweight）
    """
    plans = {
        "beginner": {
            "lose_weight": {
                "schedule": "每周训练" + str(days_per_week) + "天，每次45-60分钟",
                "focus": "以有氧为主，辅以基础力量训练",
                "workouts": [
                    {
                        "day": "第1天 - 全身力量+有氧",
                        "exercises": [
                            "热身：慢跑5分钟",
                            "深蹲 3组×12次",
                            "俯卧撑（可跪姿）3组×10次",
                            "哑铃划船 3组×12次",
                            "平板支撑 3组×30秒",
                            "有氧：快走/慢跑30分钟"
                        ]
                    },
                    {
                        "day": "第2天 - 有氧+核心",
                        "exercises": [
                            "热身：开合跳3分钟",
                            "跳绳或快走35分钟",
                            "卷腹 3组×15次",
                            "俄罗斯转体 3组×20次",
                            "拉伸放松10分钟"
                        ]
                    },
                    {
                        "day": "第3天 - 全身力量+有氧",
                        "exercises": [
                            "热身：动态拉伸5分钟",
                            "弓步蹲 3组×12次（每侧）",
                            "哑铃推举 3组×12次",
                            "高位下拉 3组×12次",
                            "臀桥 3组×15次",
                            "有氧：椭圆机或动感单车25分钟"
                        ]
                    }
                ]
            },
            "gain_muscle": {
                "schedule": "每周训练" + str(days_per_week) + "天，每次50-60分钟",
                "focus": "以复合动作为主，逐步增加负重",
                "workouts": [
                    {
                        "day": "第1天 - 胸+三头",
                        "exercises": [
                            "热身：慢跑5分钟",
                            "杠铃卧推 4组×10次",
                            "上斜哑铃飞鸟 3组×12次",
                            "绳索下压 3组×12次",
                            "俯卧撑 3组×力竭"
                        ]
                    },
                    {
                        "day": "第2天 - 背+二头",
                        "exercises": [
                            "热身：划船机5分钟",
                            "引体向上/高位下拉 4组×10次",
                            "杠铃划船 4组×10次",
                            "哑铃弯举 3组×12次",
                            "锤式弯举 3组×12次"
                        ]
                    },
                    {
                        "day": "第3天 - 腿+肩",
                        "exercises": [
                            "热身：动态拉伸5分钟",
                            "杠铃深蹲 4组×10次",
                            "腿举 3组×12次",
                            "哑铃推举 4组×10次",
                            "侧平举 3组×15次",
                            "腿弯举 3组×12次"
                        ]
                    }
                ]
            }
        }
    }

    # 获取对应计划，如果没有精确匹配则提供通用建议
    level_plans = plans.get(fitness_level, plans["beginner"])
    plan = level_plans.get(goal, level_plans.get("gain_muscle"))

    # 添加通用建议
    plan["tips"] = [
        "训练前充分热身，训练后拉伸放松",
        "每个动作注意正确姿势，避免受伤",
        "组间休息60-90秒",
        "循序渐进，逐步增加重量",
        "保证充足睡眠（7-8小时），促进恢复"
    ]

    if fitness_level == "beginner":
        plan["tips"].append("建议找专业教练指导动作，养成正确训练习惯")

    return json.dumps(plan, ensure_ascii=False, indent=2)


@tool
def answer_fitness_question(question: str) -> str:
    """回答健身、营养、运动相关的常见问题，高风险问题转就医建议。

    Args:
        question: 用户的健身相关问题
    """
    # 高风险问题优先转就医，不做运动建议
    red_flags = {
        "胸痛": "胸痛、胸闷可能与心血管问题有关。请立即停止运动，静坐休息；如果症状持续、伴随出汗、恶心或放射到手臂/下颌，请立刻就医或拨打急救电话。",
        "胸闷": "胸闷可能与心血管或呼吸系统问题有关。请立即停止运动并休息；若症状持续或加重，请尽快就医，不要自行继续训练。",
        "晕厥": "运动中晕厥需要引起重视。请立即停止运动、平卧并寻求他人帮助；反复晕厥应尽快就医排查原因。",
        "半月板": "半月板损伤需要专业评估，不建议自行尝试深蹲或负重训练。请先就医，到骨科或运动医学科明确损伤程度后再制定康复方案。",
        "怀孕": "孕期运动需要产科医生结合个人情况评估。请先咨询医生，不要自行进行腹肌训练或高强度训练。",
        "骨折": "疑似骨折请立即停止运动并固定患处，尽快就医，不要自行复位或继续负重。",
    }
    for keyword, answer in red_flags.items():
        if keyword in question:
            return answer

    # 常见问题的快速回答
    common_answers = {
        "增肌": """增肌的关键要素：
1. **训练**：每周3-5次力量训练，注重复合动作（深蹲、硬拉、卧推、划船、推举）
2. **饮食**：热量盈余300-500千卡，蛋白质每公斤体重1.6-2.2g
3. **休息**：保证7-8小时睡眠，同一肌群训练间隔48-72小时
4. **渐进超负荷**：逐步增加重量或次数
5. **一致性**：坚持至少3-6个月才能看到明显效果""",

        "减脂": """减脂的核心原则：
1. **热量缺口**：每日热量缺口300-500千卡
2. **有氧运动**：每周3-5次，每次30-45分钟
3. **力量训练**：保持肌肉量，防止基础代谢下降
4. **饮食**：高蛋白、适量碳水、低脂肪
5. **避免**：含糖饮料、油炸食品、过度节食""",

        "深蹲": """深蹲要点：
1. **站距**：与肩同宽或略宽
2. **下蹲**：膝盖与脚尖方向一致，蹲至大腿平行或略低于膝盖
3. **背部**：保持挺直，核心收紧
4. **呼吸**：下蹲吸气，站起呼气
5. **膝盖疼**：检查姿势是否正确，可能是膝盖内扣或重心前移""",

        "蛋白粉": """蛋白粉使用建议：
1. **类型**：乳清蛋白（快速吸收）适合训练后，酪蛋白（慢速吸收）适合睡前
2. **用量**：每次20-30g，每日蛋白质总量的补充
3. **时机**：训练后30分钟内效果最佳
4. **注意**：蛋白粉是补充，不能替代正常饮食
5. **选择**：选择正规品牌，注意蛋白质含量和添加剂""",

        "训练前": """训练前饮食建议：
1. **时间**：训练前 1-2 小时吃正餐，训练前 30-60 分钟可少量加餐
2. **碳水**：优先选择易消化的碳水，如香蕉、燕麦、全麦面包
3. **蛋白**：搭配 20-30g 蛋白质，如鸡蛋、酸奶、鸡胸肉
4. **避免**：高脂肪、高纤维和大量乳制品，减少肠胃不适
5. **补水**：训练前 1-2 小时饮水 300-500ml""",

        "肌酸": """肌酸使用建议：
1. **剂量**：每天 3-5g 一水肌酸即可，不一定需要冲击期
2. **时间**：训练前后均可，关键是每天坚持
3. **饮水**：保证充足饮水，肾功能异常者先咨询医生
4. **效果**：主要提升高强度短时间运动表现，不是减脂补剂
5. **选择**：优先选择成分单一的一水肌酸""",

        "酸痛": """训练后酸痛的处理：
1. **判断**：延迟性肌肉酸痛通常在 24-72 小时达到高峰
2. **恢复**：保证睡眠、蛋白质和水分摄入
3. **活动**：可进行低强度有氧、拉伸或散步，促进恢复
4. **训练**：酸痛明显时降低强度，避免同一肌群连续高强度训练
5. **就医**：如果出现尖锐疼痛、肿胀、尿液颜色异常，请及时就医""",

        "膝盖": """膝盖疼痛的处理：
1. **先停**：暂停深蹲、跑步等加重疼痛的动作
2. **观察**：注意疼痛位置、是否肿胀、是否有弹响或卡住
3. **调整**：检查动作姿势、训练量和鞋子，避免膝盖内扣
4. **恢复**：可做低强度股四头肌和臀部训练，循序渐进
5. **就医**：疼痛持续超过一周、明显肿胀或影响行走时，请咨询医生""",

        "晚上": """晚间加餐建议：
1. **蛋白**：无糖酸奶、牛奶、鸡蛋或少量鸡胸肉
2. **蔬菜**：黄瓜、番茄、生菜等低热量蔬菜
3. **碳水**：如果当天热量有缺口，可加一小份燕麦或水果
4. **避免**：高糖零食、油炸食品和大量酒精
5. **时间**：睡前 1-2 小时吃完，避免影响睡眠""",
    }

    # 检查是否有常见问题的快速回答
    for keyword, answer in common_answers.items():
        if keyword in question:
            return answer

    # 通用回答
    return f"""关于您的问题「{question}」，以下是一些建议：

1. 健身是一个长期过程，需要耐心和坚持
2. 训练、饮食、休息三者缺一不可
3. 建议根据个人情况制定适合的计划
4. 如有伤病或特殊情况，请咨询专业医生或教练
5. 保持积极心态，享受运动的过程

如需更详细的信息，建议咨询专业健身教练或查阅权威健身资料。"""


# 导出所有工具
fitness_tools = [
    calculate_bmi,
    calculate_calories,
    generate_diet_plan,
    generate_workout_plan,
    answer_fitness_question
]
