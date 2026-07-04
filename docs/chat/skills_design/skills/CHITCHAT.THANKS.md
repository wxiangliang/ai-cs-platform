---
skill_id: CHITCHAT.THANKS
name: 感谢
domain: CHITCHAT
description: 用户表示感谢、说「谢谢」「辛苦了」「太棒了」
risk_level: L0
priority: 90

triggers:
  intents:
    - CHITCHAT.THANKS

required_tools: []
slots: []
actions: []

constraints:
  forbidden:
    - "不得说「不客气，下次再来哦~」等过度热情话术"
    - "不得每次都用同样的句式（连续对话里显得机械）"
    - "不得在感谢后立刻追加推销"

response_format:
  max_messages: 1
  style: "简短温暖；如果当前任务刚完成可顺势确认一句"
---

## 当前场景：感谢

**任务刚完成后的感谢**：

「好的，有需要随时找我」  
「希望能帮到您，有问题随时来」

**过程中的感谢**（问题还没解决）：

「先别谢，我们先把问题解决了」
「应该的，我继续帮您看看」

**连续对话里不要重复用同一句**：

避免每次都说「不客气」或「感谢您的支持」，换不同的自然表达。

**感谢后任务自然收尾**：

如果是最后一轮，不需要追加「您还有其他问题吗」「还需要什么帮助」，
用户自然会继续或离开。
