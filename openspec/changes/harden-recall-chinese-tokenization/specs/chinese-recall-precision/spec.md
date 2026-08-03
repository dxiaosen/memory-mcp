## ADDED Requirements

### Requirement: Recall word overlap uses real Chinese tokenization
The application-layer text relevance scoring SHALL use a pluggable tokenizer that segments unspaced CJK text into meaningful words. The `core.domain` layer SHALL define a `MemoryTokenizer` protocol and a `SimpleTokenizer` fallback that does not depend on external libraries. The composition boundary SHALL inject a production tokenizer implementation; tokenizers SHALL discard pure-punctuation tokens so that hyphenated identifiers do not produce spurious word overlap.

#### Scenario: Unspaced Chinese query and target share a word
- **WHEN** the query is `看好新能源` and a memory revision content contains `锂电池新能源前景`
- **THEN** the tokenizer produces a non-empty word intersection including `新能源` and word overlap contributes to the relevance score

#### Scenario: Hyphenated ASCII identifier does not create false overlap
- **WHEN** the query is `zxqv-unique-778899` and a memory subject is `alpha-research`
- **THEN** the tokenizer discards the hyphen and the word intersection is empty

#### Scenario: Tokenizer is deterministic for offline evaluation
- **WHEN** the same input text is tokenized twice during an offline evaluation run
- **THEN** the tokenizer produces identical token sequences

### Requirement: Recall scoring constants are named and calibrated
The application-layer relevance threshold, relation boost, profile hint boost and subject exact-match boost SHALL be named module-level constants. The subject exact-match boost SHALL NOT exceed `0.2` so that a subject match with irrelevant content does not outrank a memory with high content relevance.

#### Scenario: Subject match with irrelevant content
- **WHEN** a memory's subject exactly matches the query subject but its content has low text relevance
- **THEN** its final score does not clamp to `1.0` and a memory with higher content relevance can outrank it

#### Scenario: Calibration does not regress offline benchmark
- **WHEN** the offline deterministic evaluation runs after constant calibration
- **THEN** `recall_at_k` does not decrease below the recorded baseline

### Requirement: Token estimation reflects character category
The rendered-context token estimation SHALL estimate CJK characters at approximately one token per character and ASCII characters at approximately one token per four characters. The estimation SHALL NOT use a single character-count divisor that underestimates CJK token counts.

#### Scenario: Pure Chinese context
- **WHEN** the rendered context contains 30 CJK characters
- **THEN** the estimated token count is at least 30

#### Scenario: Pure ASCII context
- **WHEN** the rendered context contains 40 ASCII characters
- **THEN** the estimated token count is approximately 10

#### Scenario: Mixed Chinese and English context
- **WHEN** the rendered context contains both CJK characters and ASCII words
- **THEN** the estimated token count sums the per-category estimates
