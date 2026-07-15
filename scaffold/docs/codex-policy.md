# Codex Policy — Codex 写码合同（owner 维护，Codex 只读）

> 本文件是 Codex 写代码时必须遵循的**所有 skills、代码规则、硬前提、边界**的唯一声明处。
> owner 手改；Codex 只读（列入 CLAUDE.md locked files，且为**独立治理域**——修改须 feature 显式针对 Codex-policy 且 Planner Scope 点名本文件，见 Builder 规则 3/18）。
> Builder 每轮 build **重新读取**本文件（subagent 无记忆，必重读），组装进 Codex prompt；改完下一轮 `/run` 即生效。增删 skill / 调规则只改这里，不动 harness 其他模块。

## Required Skills（Codex 写码前必须遵循）

- **ponytail** —— 最小代码纪律。写码前先过决策阶梯：
  1. 是否需要存在？（不需要则跳过）
  2. 代码库里已有？（复用）
  3. stdlib 覆盖？（用 stdlib）
  4. 原生平台特性？（用它）
  5. 已装依赖？（用它）
  6. 一行能解决？（用它）
  7. 才写最小所需。

  目的：避免死代码 / 冗余 / 过度工程。

## Code Rules（项目级代码规范）

- （此处由 owner 填写项目代码规则：命名约定、错误处理风格、目录结构等。当前留空。）

## Hard Prerequisites（硬前提 —— check 失败则 Builder 判 `blocked`，不消耗 attempt）

每项声明**可执行的 check 方法**与缺失行为，供 Builder 机械执行（Builder 不需理解 skill，只跑 check；**不得仅凭 Codex 自报**）。

> **check 安全约束（owner 写 check 时必须遵守）**：check 命令**只能是明显只读、非破坏性**的（如 `ls` / `test -d` / `cat` 读取）。**严禁** check 涉及写入、删除、网络安装、读取 `.env`/密钥、或任何破坏性命令。Builder 遇到不满足此约束的 check 时**不执行**，直接判 `blocked` + `reason_category: prerequisite` 上报 owner（见 Builder 规则 4a）。

```
- id: ponytail
  required: true
  check: |
    检查 Codex 插件缓存中是否存在 ponytail：
      ls -d ~/.codex/plugins/cache/ponytail/ponytail/*/
    返回非空（含版本目录）即视为已安装；命令出错或为空即视为缺失。
    （注：本机 Codex CLI v0.124.0 无 `codex plugin list` 子命令，故用目录存在性检查。
      若未来 Codex 提供官方“列出已装插件”命令，owner 可将 check 换成该命令。）
  on_missing: blocked
  reason_category: prerequisite
```

未来新增 skill 若有安装/工具前提，按同样结构追加 `id / required / check / on_missing / reason_category`，并保证 check 可执行、可审计。

## Bounded Minimality（不可削减项 —— 最小代码纪律的边界）

最小代码、复用优先**永远不得削减**以下任何一项：

- required behavior（spec 声明的行为）
- acceptance criteria（验收标准）
- validation（校验）
- error handling（错误处理）
- security（安全）
- accessibility（可访问性）
- data protection（数据保护）
- required docs（必要文档）

“写更少”只针对死代码 / 冗余 / 过度工程，绝不针对上述必需保障。
