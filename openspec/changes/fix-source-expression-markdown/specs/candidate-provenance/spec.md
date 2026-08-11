## MODIFIED Requirements

### Requirement: source_expression provenance validation tolerates Markdown emphasis marks
The extraction SHALL validate each candidate's `source_expression` against the source turn text using multi-level normalization containment. Validation SHALL pass when the expression matches via: raw containment; whitespace-normalized containment; whitespace-removed compact containment; OR Markdown-emphasis-stripped compact containment (removing paired `*`, `_`, `` ` ``, `~` characters then compact comparison). This tolerates models that strip Markdown formatting marks from the source expression while still rejecting substantive rewrites (added/removed words, changed punctuation, altered digits do NOT match even after stripping).

#### Scenario: Source expression with stripped Markdown bold marks is accepted
- **WHEN** the source turn contains `**库存周转天数有了硬天花板**` and the model's source_expression is `库存周转天数有了硬天花板` (bold marks stripped)
- **THEN** the candidate passes validation and is NOT discarded as `invalid_source_expression`

#### Scenario: Source expression with stripped inline code marks is accepted
- **WHEN** the source turn contains `` `memory_id` `` and the model's source_expression is `memory_id` (backticks stripped)
- **THEN** the candidate passes validation

#### Scenario: Substantive rewrite is still rejected after Markdown stripping
- **WHEN** the source turn contains `**毛利率 41%**` and the model's source_expression is `毛利率达到了百分之四十一的高位` (paraphrased, not just stripped marks)
- **THEN** the candidate is discarded as `invalid_source_expression`

### Requirement: Extraction prompt requires verbatim source_expression
The candidate extraction system prompt SHALL instruct the model that `source_expression` MUST be a verbatim contiguous substring of the original message, preserving every character including punctuation, digits, and Markdown emphasis marks (`**`, `_`, `` ` ``, `~~`) exactly as they appear, and SHALL NOT clean, paraphrase, or strip formatting.

#### Scenario: Prompt contains verbatim preservation instruction
- **WHEN** the candidate extraction prompt is rendered
- **THEN** it contains an instruction that source_expression must preserve Markdown emphasis marks verbatim
