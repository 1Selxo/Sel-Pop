/*! @license
 * Build entry for Sel-Pop's embedded Yomitan language engine.
 *
 * Yomitan is Copyright (C) 2024-2026 Yomitan Authors and licensed under
 * GPL-3.0-or-later. This project is GPL-3.0, and the generated bundle keeps
 * the upstream source notices emitted by esbuild's legal-comments option.
 * The checked-in bundle is generated from Yomitan revision e2ed450c2f11.
 *
 * Build from a sibling checkout:
 *   npx esbuild scripts/yomitan_language_bundle_entry.mjs \
 *     --bundle --format=iife --platform=browser --target=es2022 \
 *     --legal-comments=inline --outfile=src/dictionary/yomitan_language_bundle.js
 */

import {LanguageTransformer} from '../../yomitan/ext/js/language/language-transformer.js';
import {languageDescriptorMap} from '../../yomitan/ext/js/language/language-descriptors.js';

const MAX_VARIANTS = 4096;
const transformers = new Map();

for (const [language, descriptor] of languageDescriptorMap) {
    if (!descriptor.languageTransforms) { continue; }
    const transformer = new LanguageTransformer();
    transformer.addDescriptor(descriptor.languageTransforms);
    transformers.set(language, transformer);
}

function getTextVariants(text, processors) {
    let variants = new Map([[text, [[]]]]);
    for (const [id, processor] of Object.entries(processors || {})) {
        const next = new Map();
        for (const [variant, chains] of variants) {
            for (const processed of processor.process(variant).slice(0, MAX_VARIANTS)) {
                const existing = next.get(processed) || [];
                if (processed === variant) {
                    existing.push(...chains);
                } else {
                    existing.push(...chains.map((chain) => [...chain, id]));
                }
                next.set(processed, existing);
            }
        }
        variants = new Map([...next].slice(0, MAX_VARIANTS));
    }
    return variants;
}

function getValidPartsOfSpeech(descriptor, transformer, conditions) {
    if (!conditions || !descriptor.languageTransforms) { return []; }
    const valid = [];
    for (const [condition, details] of Object.entries(descriptor.languageTransforms.conditions)) {
        if (!details.isDictionaryForm) { continue; }
        const flags = transformer.getConditionFlagsFromPartsOfSpeech([condition]);
        if (LanguageTransformer.conditionsMatch(conditions, flags)) {
            valid.push(condition);
        }
    }
    return valid;
}

function expand(language, text) {
    const descriptor = languageDescriptorMap.get(language);
    if (!descriptor) { return [{text, process: [], processors: [], validPartsOfSpeech: []}]; }

    const transformer = transformers.get(language);
    const transformNames = descriptor.languageTransforms?.transforms || {};
    const output = new Map();
    const preprocessed = getTextVariants(text, descriptor.textPreprocessors);

    for (const [source, preprocessorChains] of preprocessed) {
        const transformed = transformer ?
            transformer.transform(source) :
            [{text: source, conditions: 0, trace: []}];

        for (const result of transformed) {
            const postprocessed = getTextVariants(result.text, descriptor.textPostprocessors);
            for (const [finalText, postprocessorChains] of postprocessed) {
                const process = result.trace.map(({transform}) => transformNames[transform]?.name || transform);
                const processors = [
                    ...(preprocessorChains[0] || []),
                    ...(postprocessorChains[0] || []),
                ];
                const validPartsOfSpeech = transformer ?
                    getValidPartsOfSpeech(descriptor, transformer, result.conditions) : [];
                const key = `${finalText}\u0000${validPartsOfSpeech.join(' ')}`;
                if (!output.has(key)) {
                    output.set(key, {text: finalText, process, processors, validPartsOfSpeech});
                }
                if (output.size >= MAX_VARIANTS) { return [...output.values()]; }
            }
        }
    }
    return [...output.values()];
}

globalThis.selPopYomitanExpandJson = (language, text) => JSON.stringify(expand(language, text));
globalThis.selPopYomitanLanguageInfoJson = () => JSON.stringify(
    [...languageDescriptorMap].map(([iso, descriptor]) => ({
        iso,
        name: descriptor.name,
        hasTransforms: transformers.has(iso),
        preprocessors: Object.keys(descriptor.textPreprocessors || {}),
        postprocessors: Object.keys(descriptor.textPostprocessors || {}),
    })),
);
