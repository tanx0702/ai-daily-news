/**
 * Multi-language tokenizer for spec-superflow keyword extraction.
 * Supports English (stemming + stop words) and Chinese (CJK sliding window).
 * Zero external dependencies.
 */
/**
 * Auto-detect the language of the given text.
 * Returns 'en', 'zh', or 'mixed'.
 */
export declare function detectLanguage(text: string): 'en' | 'zh' | 'mixed';
/**
 * Main tokenizer entry point.
 * @param text - The text to tokenize.
 * @param language - 'auto' | 'en' | 'zh'. Defaults to 'auto'.
 * @returns Set of token strings.
 */
export declare function tokenize(text: string, language?: 'auto' | 'en' | 'zh'): Set<string>;
