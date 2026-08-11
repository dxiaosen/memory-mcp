## MODIFIED Requirements

### Requirement: Relation extraction prompt forbids stored memory content as source_expression
The relation extraction system prompt SHALL instruct the model that `source_expression` MUST come from the `source_turn` text, NOT from an endpoint memory's stored content. The model SHALL NOT use a memory record's `content` field (or any paraphrase of it) as the `source_expression` for a relation, because the source of a relation is the user's utterance in the conversation, not the stored memory.

#### Scenario: Prompt forbids using stored memory content as source_expression
- **WHEN** the relation extraction prompt is rendered
- **THEN** it contains an instruction that source_expression must come from source_turn, not from endpoint memory content

### Requirement: Relation extraction prompt requires verbatim source_expression
The relation extraction system prompt SHALL instruct the model that `source_expression` MUST be a verbatim contiguous substring of the original message, preserving every character including punctuation, digits, and Markdown emphasis marks (`**`, `_`, `` ` ``, `~~`) exactly as they appear.

#### Scenario: Prompt contains verbatim preservation instruction for relations
- **WHEN** the relation extraction prompt is rendered
- **THEN** it contains an instruction that relation source_expression must preserve Markdown emphasis marks verbatim
