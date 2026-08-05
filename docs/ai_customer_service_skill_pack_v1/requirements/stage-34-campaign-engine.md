# Stage 34：Campaign Engine

## 目标

建立活动事实、资格、时效、频控、抑制和审计能力。

## 单一事实来源

活动必须来自机器可读 registry 或外部 Campaign Tool。

## 必须实现

- enabled；
- valid_from / valid_to；
- eligible customer；
- eligible product；
- benefit；
- conditions；
- frequency cap；
- opt-out；
- suppression；
- required disclosures。

## 验收

- 过期活动不展示；
- 不符合资格时不暗示用户符合；
- 用户拒绝后不重复；
- 投诉和退款中不展示；
- 所有展示可追溯到活动版本。
