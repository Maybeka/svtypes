/* Prototype coverpoint domain editor.  Edits stay in the page; they do not write source. */
(function (global) {
    "use strict";

    const CELL_LIMIT = 32;
    const PALETTE = ["#3b82f6", "#059669", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#4f46e5"];
    const drafts = new Map();
    const transitionDrafts = new Map();
    const histories = new Map();
    const HISTORY_LIMIT = 100;
    let activeHistoryHost = null;
    let historyShortcutsBound = false;

    function esc(value) {
        return String(value ?? "").replace(/[&<>"]/g, (ch) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
        }[ch]));
    }

    function integerValue(value) {
        if (typeof value === "number" && Number.isInteger(value)) {
            return value;
        }
        if (value && typeof value === "object") {
            if ((value.kind === "constant" || value.kind === "enum_literal") && Number.isInteger(value.value)) {
                return value.value;
            }
        }
        return null;
    }

    function rangesFromSelector(selector) {
        if (!selector || typeof selector !== "object") {
            return null;
        }
        if (selector.kind === "constant" || selector.kind === "enum_literal") {
            const value = integerValue(selector);
            return value == null ? null : [[value, value]];
        }
        if (selector.kind === "range") {
            const lower = integerValue(selector.lower);
            const upper = integerValue(selector.upper);
            if (lower == null || upper == null || lower > upper) {
                return null;
            }
            return [[lower, upper]];
        }
        if (selector.kind === "values") {
            const ranges = [];
            for (const item of selector.items || []) {
                const nested = rangesFromSelector(item);
                if (!nested) {
                    return null;
                }
                ranges.push(...nested);
            }
            return ranges;
        }
        if (selector.kind === "array_split") {
            return rangesFromSelector(selector.selector);
        }
        return null;
    }

    function mergeRanges(ranges) {
        const ordered = ranges.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
        const merged = [];
        for (const [lo, hi] of ordered) {
            const last = merged[merged.length - 1];
            if (last && lo <= last[1] + 1) {
                last[1] = Math.max(last[1], hi);
            } else {
                merged.push([lo, hi]);
            }
        }
        return merged;
    }

    function cloneItem(item) {
        return {
            name: item.name,
            kind: item.kind,
            lo: item.lo,
            hi: item.hi,
            fragments: item.fragments.map((pair) => pair.slice()),
            contiguous: item.contiguous,
            array: Boolean(item.array),
            arrayCount: item.array ? item.arrayCount ?? "auto" : null,
            automatic: Boolean(item.automatic),
            editingFragment: Number.isInteger(item.editingFragment) ? item.editingFragment : null,
            original: item.original,
        };
    }

    function snapshotModel(model) {
        return {
            selected: model.selected,
            selectedFragment: model.selectedFragment,
            editingFragment: model.editingFragment,
            armedEdge: model.armedEdge,
            min: model.min,
            max: model.max,
            items: model.items.map(cloneItem),
        };
    }

    function sameSnapshot(left, right) {
        return JSON.stringify(left) === JSON.stringify(right);
    }

    function historyFor(host) {
        const key = host.dataset.canvasKey;
        if (!key) {
            return null;
        }
        let history = histories.get(key);
        if (!history) {
            history = {undo: [], redo: [], current: null};
            histories.set(key, history);
        }
        return history;
    }

    function recordHistory(host, model) {
        const history = historyFor(host);
        if (!history) {
            return;
        }
        const next = snapshotModel(model);
        if (!history.current) {
            history.current = next;
            return;
        }
        if (sameSnapshot(history.current, next)) {
            return;
        }
        history.undo.push(history.current);
        if (history.undo.length > HISTORY_LIMIT) {
            history.undo.shift();
        }
        history.redo = [];
        history.current = next;
    }

    function applySnapshot(model, saved) {
        model.items = saved.items.map(cloneItem);
        model.selected = saved.selected;
        model.selectedFragment = Number.isInteger(saved.selectedFragment) ? saved.selectedFragment : 0;
        model.editingFragment = Number.isInteger(saved.editingFragment) ? saved.editingFragment : null;
        model.armedEdge = saved.armedEdge || "hi";
        if (!model.enum) {
            applyDomain(model, saved.min, saved.max);
        }
        syncAutomatic(model);
        syncDefault(model);
    }

    function undo(host, model, original) {
        const history = historyFor(host);
        if (!history?.undo.length) {
            return;
        }
        history.redo.push(history.current);
        history.current = history.undo.pop();
        applySnapshot(model, history.current);
        render(host, model, original);
    }

    function redo(host, model, original) {
        const history = historyFor(host);
        if (!history?.redo.length) {
            return;
        }
        history.undo.push(history.current);
        history.current = history.redo.pop();
        applySnapshot(model, history.current);
        render(host, model, original);
    }

    function transitionSelectorText(bin) {
        if (bin.display_selector) {
            return String(bin.display_selector);
        }
        const text = (selector) => {
            if (!selector || typeof selector !== "object") {
                return "—";
            }
            if (selector.kind === "constant") {
                return String(selector.value);
            }
            if (selector.kind === "enum_literal") {
                return `${selector.type_name}.${selector.member}`;
            }
            if (selector.kind === "range") {
                return `[${text(selector.lower)}:${text(selector.upper)}]`;
            }
            if (selector.kind === "values") {
                return (selector.items || []).map(text).join(" → ");
            }
            return "已定义选择器";
        };
        return text(bin.selector);
    }

    function transitionStepText(selector) {
        if (!selector || typeof selector !== "object") return "—";
        if (selector.kind === "constant") return String(selector.value);
        if (selector.kind === "enum_literal") return `${selector.type_name}.${selector.member}`;
        if (selector.kind === "range") return `[${transitionStepText(selector.lower)}:${transitionStepText(selector.upper)}]`;
        if (selector.kind === "values") return `{${(selector.items || []).map(transitionStepText).join(", ")}}`;
        return transitionSelectorText({selector});
    }

    function transitionModelFromBins(bins) {
        return {bins: (bins || []).filter((bin) => bin.kind === "transition").map((bin) => ({
            name: bin.name,
            steps: (bin.selector?.kind === "values" ? bin.selector.items : [bin.selector]).map((selector) => selector?.kind === "repeat"
                ? {repeat: true, text: transitionStepText(selector.term), selector: selector.term, minimum: selector.minimum, maximum: selector.maximum, editing: false}
                : {repeat: false, text: transitionStepText(selector), selector, editing: false}),
            coverageKind: bin.coverage_kind || "normal",
            array: Boolean(bin.array),
        }))};
    }

    function cloneTransitionModel(model) {
        return JSON.parse(JSON.stringify(model));
    }

    function transitionHistory(host) {
        return historyFor(host);
    }

    function recordTransitionHistory(host, model) {
        const history = transitionHistory(host);
        if (!history) return;
        const next = cloneTransitionModel(model);
        if (!history.current) { history.current = next; return; }
        if (sameSnapshot(history.current, next)) return;
        history.undo.push(history.current);
        if (history.undo.length > HISTORY_LIMIT) history.undo.shift();
        history.redo = [];
        history.current = next;
    }

    function transitionUndo(host, model) {
        const history = transitionHistory(host);
        if (!history?.undo.length) return;
        history.redo.push(history.current);
        history.current = history.undo.pop();
        Object.assign(model, cloneTransitionModel(history.current));
        renderTransitionEditor(host, model);
    }

    function transitionRedo(host, model) {
        const history = transitionHistory(host);
        if (!history?.redo.length) return;
        history.undo.push(history.current);
        history.current = history.redo.pop();
        Object.assign(model, cloneTransitionModel(history.current));
        renderTransitionEditor(host, model);
    }

    function transitionSyntax(bin) {
        return bin.steps.map((step) => `${step.text}${step.repeat ? `[*${step.minimum}:${step.maximum}]` : ""}`).join(" → ");
    }

    function syncTransitionBinsPanel(host, model) {
        if (model.embedded) {
            const stateHost = host.closest(".bin-canvas-host")?.querySelector(".transition-state-host");
            if (stateHost?._binModel) {
                stateHost._binModel.extraBins = model.bins.map((bin) => ({name: bin.name, selector: transitionSyntax(bin)}));
                syncBinsPanel(stateHost, stateHost._binModel);
            }
            return;
        }
        const panel = host.closest(".bin-canvas-panel")?.parentElement?.querySelector(".bins-panel .panel-body");
        if (!panel) return;
        const rows = model.bins.map((bin) => `<tr><td class="bin-name">${esc(bin.name)}</td><td>transition</td><td><code>${esc(transitionSyntax(bin))}</code></td></tr>`).join("");
        panel.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Bin</th><th>分类</th><th>值 / 选择器</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function uniqueTransitionName(model) {
        let index = 1;
        while (model.bins.some((bin) => bin.name === `transition_${index}`)) index += 1;
        return `transition_${index}`;
    }

    function activateHistoryShortcuts(host) {
        activeHistoryHost = host;
        if (historyShortcutsBound) {
            return;
        }
        historyShortcutsBound = true;
        document.addEventListener("keydown", (event) => {
            const current = activeHistoryHost;
            if (!current?.isConnected || !(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "z") {
                return;
            }
            event.preventDefault();
            if (current._transitionModel) {
                if (event.shiftKey) {
                    transitionRedo(current, current._transitionModel);
                } else {
                    transitionUndo(current, current._transitionModel);
                }
            } else if (event.shiftKey) {
                redo(current, current._binModel, current._binOriginal);
            } else {
                undo(current, current._binModel, current._binOriginal);
            }
        });
    }

    function isAutomaticBinName(name) {
        return String(name || "") === "auto" || String(name || "").startsWith("auto[");
    }

    function arrayBase(name) {
        const match = String(name || "").match(/^(.*)\[([^\]]+)\]$/);
        if (!match) {
            return null;
        }
        const inner = match[2];
        if (/^-?\d+$/.test(inner) || /^-?\d+:-?\d+$/.test(inner) || match[1] === "auto") {
            return match[1];
        }
        return null;
    }

    function inferArrayCount(members) {
        const split = members[0]?.original?.selector;
        if (split?.kind === "array_split") {
            return Number.isInteger(split.count) ? split.count : "auto";
        }
        if (members.every((item) => String(item.name).startsWith("auto["))) {
            return "auto";
        }
        const auto = members.every((item) => {
            const match = String(item.name).match(/\[(-?\d+)\]$/);
            return item.contiguous && item.lo === item.hi && match && Number(match[1]) === item.lo;
        });
        return auto ? "auto" : members.length;
    }

    function mergeArrayMembers(base, members) {
        const automatic = base === "auto" || members.every((item) => item.automatic);
        const fragments = automatic
            ? members.flatMap((item) => item.fragments)
            : mergeRanges(members.flatMap((item) => item.fragments));
        return {
            name: base,
            kind: members[0].kind,
            lo: fragments[0][0],
            hi: fragments[fragments.length - 1][1],
            fragments,
            contiguous: fragments.length === 1,
            array: !automatic,
            arrayCount: automatic ? null : inferArrayCount(members),
            automatic,
            original: members[0].original,
        };
    }

    function groupArrayItems(items) {
        const grouped = [];
        let index = 0;
        while (index < items.length) {
            const base = arrayBase(items[index].name);
            if (!base) {
                grouped.push(items[index]);
                index += 1;
                continue;
            }
            const members = [];
            while (index < items.length && arrayBase(items[index].name) === base) {
                members.push(items[index]);
                index += 1;
            }
            grouped.push(mergeArrayMembers(base, members));
        }
        return grouped;
    }

    function parseArrayCount(raw) {
        const text = String(raw ?? "").trim();
        if (/^auto$/i.test(text)) {
            return "auto";
        }
        if (!/^\d+$/.test(text)) {
            return null;
        }
        const count = Number(text);
        return count >= 1 ? count : null;
    }

    function autoBinMaxFrom(point) {
        const value = point?.options?.auto_bin_max;
        return Number.isInteger(value) && value > 0 ? value : 64;
    }

    function axisFromPoint(point) {
        const labels = {};
        const raw = point?.enum_labels;
        if (raw && typeof raw === "object") {
            for (const [value, label] of Object.entries(raw)) {
                const number = Number(value);
                if (Number.isInteger(number)) {
                    labels[number] = String(label).split(".").pop();
                }
            }
        }
        if (Object.keys(labels).length) {
            const values = Object.keys(labels).map(Number);
            const min = Math.min(...values);
            const max = Math.max(...values);
            return {min, max, nativeMin: min, nativeMax: max, labels, enum: true, autoBinMax: autoBinMaxFrom(point)};
        }
        const domain = point?.options?.comparison_domain;
        if (domain && Number.isInteger(domain.width) && domain.width >= 1 && domain.width <= 53) {
            const width = domain.width;
            const min = domain.signed ? -(2 ** (width - 1)) : 0;
            const max = domain.signed ? (2 ** (width - 1)) - 1 : (2 ** width) - 1;
            return {min, max, nativeMin: min, nativeMax: max, labels, enum: domain.kind === "enum", autoBinMax: autoBinMaxFrom(point)};
        }
        return null;
    }

    function makeDefaultItem(name, original) {
        return {
            name,
            kind: "default",
            lo: null,
            hi: null,
            fragments: [],
            contiguous: false,
            array: false,
            arrayCount: null,
            automatic: false,
            original: original || {kind: "default", name, selector: null},
        };
    }

    function uncoveredFragments(model) {
        const covered = mergeRanges(
            model.items
                .filter((item) => item.kind !== "default")
                .flatMap((item) => item.fragments)
        );
        const leftover = [];
        let cursor = model.min;
        for (const [lo, hi] of covered) {
            if (cursor < lo) {
                leftover.push([cursor, lo - 1]);
            }
            cursor = Math.max(cursor, hi + 1);
        }
        if (cursor <= model.max) {
            leftover.push([cursor, model.max]);
        }
        return leftover;
    }

    function syncDefault(model) {
        const item = model.items.find((entry) => entry.kind === "default");
        if (!item) {
            return;
        }
        item.fragments = uncoveredFragments(model);
        if (item.fragments.length) {
            item.lo = item.fragments[0][0];
            item.hi = item.fragments[item.fragments.length - 1][1];
        } else {
            item.lo = null;
            item.hi = null;
        }
    }

    function defaultItem(model) {
        return model.items.find((item) => item.kind === "default") || null;
    }

    function applyDomain(model, min, max) {
        if (model.enum) {
            return false;
        }
        if (!Number.isInteger(min) || !Number.isInteger(max)) {
            return false;
        }
        const lo = clamp(min, model.nativeMin, model.nativeMax);
        const hi = clamp(max, model.nativeMin, model.nativeMax);
        if (hi < lo) {
            return false;
        }
        model.min = lo;
        model.max = hi;
        syncAutomatic(model);
        syncDefault(model);
        return true;
    }

    function axisSpec(model) {
        return {
            min: model.min,
            max: model.max,
            nativeMin: model.nativeMin,
            nativeMax: model.nativeMax,
            labels: model.labels,
            enum: model.enum,
            autoBinMax: model.autoBinMax,
        };
    }

    function modelFromBins(bins, axis) {
        const items = [];
        const hasTransitionBins = (bins || []).some((bin) => bin.kind === "transition");
        const labels = {...(axis?.labels || {})};
        for (const bin of bins || []) {
            if (bin.kind === "transition") continue;
            if (bin.kind === "default") {
                items.push(makeDefaultItem(bin.name, bin));
                continue;
            }
            const ranges = rangesFromSelector(bin.selector);
            if (!ranges) {
                return null;
            }
            const split = bin.selector && bin.selector.kind === "array_split";
            const fragments = split || bin.selector?.kind !== "values" ? mergeRanges(ranges) : ranges;
            if (fragments.length === 1 && fragments[0][0] === fragments[0][1] && bin.display_selector) {
                labels[fragments[0][0]] = String(bin.display_selector).split(".").pop();
            }
            items.push({
                name: bin.name,
                kind: bin.kind,
                lo: fragments[0][0],
                hi: fragments[fragments.length - 1][1],
                fragments,
                contiguous: fragments.length === 1,
                array: Boolean(split),
                arrayCount: split ? (Number.isInteger(bin.selector.count) ? bin.selector.count : "auto") : null,
                automatic: isAutomaticBinName(bin.name),
                original: bin,
            });
        }
        const grouped = groupArrayItems(items);
        const values = grouped
            .filter((item) => item.kind !== "default")
            .flatMap((item) => item.fragments.flat());
        const inferredMin = values.length ? (Math.min(...values) >= 0 ? 0 : Math.min(...values)) : 0;
        const inferredMax = values.length ? Math.max(...values) : 1;
        let nativeMin;
        let nativeMax;
        if (Number.isInteger(axis?.nativeMin) && Number.isInteger(axis?.nativeMax) && axis.nativeMax >= axis.nativeMin) {
            nativeMin = axis.nativeMin;
            nativeMax = axis.nativeMax;
        } else if (Number.isInteger(axis?.min) && Number.isInteger(axis?.max) && axis.max >= axis.min) {
            nativeMin = axis.min;
            nativeMax = axis.max;
        } else if (values.length || grouped.some((item) => item.kind === "default")) {
            nativeMin = inferredMin;
            nativeMax = inferredMax < inferredMin ? inferredMin : inferredMax;
        } else {
            return null;
        }
        const isEnum = Boolean(axis?.enum);
        let min = nativeMin;
        let max = nativeMax;
        if (!isEnum && Number.isInteger(axis?.min) && Number.isInteger(axis?.max) && axis.max >= axis.min) {
            min = clamp(axis.min, nativeMin, nativeMax);
            max = clamp(axis.max, nativeMin, nativeMax);
            if (max < min) {
                min = nativeMin;
                max = nativeMax;
            }
        }
        if (!grouped.length && !axis) {
            return null;
        }
        const model = {
            min,
            max,
            nativeMin,
            nativeMax,
            enum: isEnum,
            items: grouped,
            labels,
            selected: grouped[0]?.name || null,
            selectedFragment: 0,
            editingFragment: null,
            armedEdge: "hi",
            autoBinMax: Number.isInteger(axis?.autoBinMax) && axis.autoBinMax > 0 ? axis.autoBinMax : 64,
            hasTransitionBins,
        };
        syncAutomatic(model);
        syncDefault(model);
        return model;
    }

    function colorFor(item, index) {
        if (item.kind === "default") {
            return "#b45309";
        }
        if (item.kind === "illegal") {
            return "#c53030";
        }
        if (item.kind === "ignore") {
            return "#64748b";
        }
        return PALETTE[index % PALETTE.length];
    }

    function itemIndex(model, item) {
        return model.items.indexOf(item);
    }

    function occupants(model, value) {
        return model.items.filter((item) => (
            item.kind !== "default"
            && item.fragments.some(([lo, hi]) => value >= lo && value <= hi)
        ));
    }

    function cellOwner(model, value) {
        const hits = occupants(model, value);
        if (!hits.length) {
            return null;
        }
        const selected = selectedItem(model);
        if (selected && hits.some((item) => item.name === selected.name)) {
            return selected;
        }
        return hits.find((item) => item.kind === "illegal")
            || hits.find((item) => item.kind === "ignore")
            || hits[0];
    }

    function covers(item, value) {
        return item.fragments.some(([lo, hi]) => value >= lo && value <= hi);
    }

    function leftoverCovers(model, value) {
        const leftover = defaultItem(model);
        return Boolean(leftover && leftover.fragments.some(([lo, hi]) => value >= lo && value <= hi));
    }

    function canCreateAt(model, value) {
        const hits = occupants(model, value);
        return !hits.length || hits.every((item) => item.automatic);
    }

    function hasExplicitCoverageBins(model) {
        return Boolean(model.hasTransitionBins) || model.items.some((item) => (
            !item.automatic
            && (item.kind === "normal" || item.kind === "default" || item.kind === "transition")
        ));
    }

    function automaticFragments(model) {
        const bounds = domainBounds(model);
        if (bounds.max < bounds.min) {
            return [];
        }
        if (model.enum || isEnumModel(model)) {
            return enumValues(model).map((value) => [value, value]);
        }
        const span = bounds.max - bounds.min + 1;
        const count = Math.min(span, model.autoBinMax || 64);
        const base = Math.floor(span / count);
        const remainder = span % count;
        const fragments = [];
        let current = bounds.min;
        for (let index = 0; index < count; index += 1) {
            const width = base + (index === count - 1 ? remainder : 0);
            const end = current + width - 1;
            fragments.push([current, end]);
            current = end + 1;
        }
        return fragments;
    }

    function makeAutomaticItem(fragments) {
        return {
            name: "auto",
            kind: "normal",
            lo: fragments[0][0],
            hi: fragments[fragments.length - 1][1],
            fragments: fragments.map((pair) => pair.slice()),
            contiguous: fragments.length === 1,
            array: false,
            arrayCount: null,
            automatic: true,
            original: {kind: "normal", name: "auto", selector: null},
        };
    }

    function syncAutomatic(model) {
        const existing = model.items.filter((item) => item.automatic);
        if (hasExplicitCoverageBins(model)) {
            if (!existing.length) {
                return;
            }
            const selectedAuto = existing.some((item) => item.name === model.selected);
            model.items = model.items.filter((item) => !item.automatic);
            if (selectedAuto) {
                model.selected = model.items[0]?.name || null;
                model.selectedFragment = 0;
            }
            return;
        }
        const fragments = automaticFragments(model);
        if (!fragments.length) {
            model.items = model.items.filter((item) => !item.automatic);
            return;
        }
        let item = existing[0];
        if (!item) {
            item = makeAutomaticItem(fragments);
            model.items.unshift(item);
        } else {
            item.fragments = fragments.map((pair) => pair.slice());
            refreshItemBounds(item);
        }
        model.items = model.items.filter((entry) => !entry.automatic || entry === item);
    }

    function declaredBinCount(model) {
        return model.items.reduce((total, item) => {
            if (item.kind === "default") {
                return total + 1;
            }
            if (item.automatic) {
                return total + Math.max(1, item.fragments.length);
            }
            if (item.array && Number.isInteger(item.arrayCount)) {
                return total + item.arrayCount;
            }
            if (item.array) {
                return total + Math.max(1, item.fragments.length);
            }
            return total + 1;
        }, 0);
    }

    function uncoveredValueCount(model) {
        const span = model.max - model.min + 1;
        if (span > 4096) {
            return null;
        }
        let count = 0;
        for (let value = model.min; value <= model.max; value += 1) {
            if (!occupants(model, value).length && !leftoverCovers(model, value)) {
                count += 1;
            }
        }
        return count;
    }

    function statsMarkup(model) {
        const bins = declaredBinCount(model);
        const empty = uncoveredValueCount(model);
        const extra = empty ? ` · ${empty} 个值未覆盖` : "";
        return `共 ${bins} 个 bins${extra}`;
    }

    function uniqueName(model) {
        const names = new Set(model.items.map((item) => item.name));
        let index = 1;
        while (names.has(`bin_${index}`)) {
            index += 1;
        }
        return `bin_${index}`;
    }

    function uniqueDefaultName(model) {
        const names = new Set(model.items.map((item) => item.name));
        for (const name of ["other", "default"]) {
            if (!names.has(name)) {
                return name;
            }
        }
        return uniqueName(model);
    }

    function uniqueCanvasTransitionName(model) {
        const names = new Set([
            ...model.items.map((item) => item.name),
            ...(model.transitionBins || []).map((bin) => bin.name),
        ]);
        let index = 1;
        while (names.has(`transition_${index}`)) {
            index += 1;
        }
        return `transition_${index}`;
    }

    function addTransitionBin(model) {
        const value = model.min;
        model.transitionBins ??= [];
        model.transitionBins.push({
            name: uniqueCanvasTransitionName(model),
            kind: "transition",
            coverageKind: "normal",
            array: false,
            steps: [
                {repeat: false, selector: selectorFor(value, value), text: String(value)},
                {repeat: false, selector: selectorFor(value, value), text: String(value)},
            ],
        });
        model.hasTransitionBins = true;
        return model.transitionBins.length - 1;
    }

    function addDefault(model) {
        if (defaultItem(model)) {
            return null;
        }
        const name = uniqueDefaultName(model);
        const item = makeDefaultItem(name);
        model.items.push(item);
        syncDefault(model);
        model.selected = name;
        model.selectedFragment = 0;
        model.armedEdge = "hi";
        return item;
    }

    function selectorFor(lo, hi) {
        if (lo === hi) {
            return {kind: "constant", value: lo};
        }
        return {
            kind: "range",
            lower: {kind: "constant", value: lo},
            upper: {kind: "constant", value: hi},
        };
    }

    function selectorFromFragments(fragments) {
        const selectors = fragments.map(([lo, hi]) => selectorFor(lo, hi));
        if (selectors.length === 1) {
            return selectors[0];
        }
        return {kind: "values", items: selectors};
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function domainBounds(domain) {
        const min = Number.isInteger(domain.nativeMin) ? Math.max(domain.min, domain.nativeMin) : domain.min;
        const max = Number.isInteger(domain.nativeMax) ? Math.min(domain.max, domain.nativeMax) : domain.max;
        return {min, max};
    }

    function refreshItemBounds(item) {
        if (item.kind === "default") {
            return;
        }
        if (!item.fragments.length) {
            item.lo = null;
            item.hi = null;
            item.contiguous = true;
            item.syncSelector?.();
            return;
        }
        item.lo = Math.min(...item.fragments.map((frag) => frag[0]));
        item.hi = Math.max(...item.fragments.map((frag) => frag[1]));
        item.contiguous = item.fragments.length === 1;
        item.syncSelector?.();
    }

    function fragmentIndex(model, item) {
        const index = model.selectedFragment;
        if (Number.isInteger(index) && index >= 0 && index < item.fragments.length) {
            return index;
        }
        return item.fragments.length ? 0 : -1;
    }

    function packTracks(pieces) {
        const ordered = pieces.slice().sort((left, right) => left.lo - right.lo || left.hi - right.hi || left.index - right.index);
        const tracks = [];
        for (const piece of ordered) {
            let placed = false;
            for (const track of tracks) {
                const hit = track.some((other) => other.lo <= piece.hi && piece.lo <= other.hi);
                if (!hit) {
                    track.push(piece);
                    placed = true;
                    break;
                }
            }
            if (!placed) {
                tracks.push([piece]);
            }
        }
        return tracks;
    }

    function visibleFragmentItems(model, item) {
        const visible = [];
        item.fragments.forEach(([lo, hi], index) => {
            const left = Math.max(lo, model.min);
            const right = Math.min(hi, model.max);
            if (left <= right) {
                visible.push({index, lo: left, hi: right});
            }
        });
        return visible;
    }

    function firstOpenValue(model, item) {
        const bounds = domainBounds(model);
        let cursor = bounds.min;
        for (const [lo, hi] of mergeRanges(item.fragments)) {
            if (cursor < lo) {
                return cursor;
            }
            cursor = Math.max(cursor, hi + 1);
        }
        return cursor <= bounds.max ? cursor : bounds.min;
    }

    function firstUncoveredValue(model) {
        for (let value = model.min; value <= model.max && value - model.min < 4096; value += 1) {
            if (!occupants(model, value).length && !leftoverCovers(model, value)) {
                return value;
            }
        }
        return model.min;
    }

    function addFragment(item, lo, hi, domain) {
        if (item.kind === "default" || item.automatic) {
            return -1;
        }
        const bounds = domainBounds(domain);
        const left = clamp(Math.min(lo, hi), bounds.min, bounds.max);
        const right = clamp(Math.max(lo, hi), bounds.min, bounds.max);
        item.fragments.push([left, right]);
        refreshItemBounds(item);
        return item.fragments.length - 1;
    }

    function removeFragment(item, index) {
        if (item.kind === "default" || item.automatic || !item.fragments[index]) {
            return;
        }
        item.fragments.splice(index, 1);
        refreshItemBounds(item);
    }

    function selectedItem(model) {
        if (model.transitionActive) {
            const step = model.transitionBins?.[model.transitionActive.bin]?.steps[model.transitionActive.step];
            if (!step) return null;
            if (!step.editor) {
                const fragments = rangesFromSelector(step.selector);
                if (!fragments) return null;
                step.editor = {name: `transition:${model.transitionActive.bin}:${model.transitionActive.step}`, kind: "normal", lo: fragments[0][0], hi: fragments[fragments.length - 1][1], fragments: fragments.map((pair) => pair.slice()), contiguous: fragments.length === 1, array: false, automatic: false};
                step.editor.syncSelector = () => {
                    step.selector = selectorFromFragments(step.editor.fragments);
                    step.text = transitionStepText(step.selector);
                };
            }
            return step.editor;
        }
        return model.items.find((item) => item.name === model.selected) || null;
    }

    function usesCells(model) {
        return model.max - model.min + 1 <= CELL_LIMIT;
    }

    function transitionSelectorItem(model, binIndex, stepIndex) {
        const step = model.transitionBins?.[binIndex]?.steps[stepIndex];
        if (!step) return null;
        if (!step.editor) {
            const fragments = rangesFromSelector(step.selector);
            if (!fragments) return null;
            step.editor = {name: `transition:${binIndex}:${stepIndex}`, kind: "normal", lo: fragments[0][0], hi: fragments[fragments.length - 1][1], fragments: fragments.map((pair) => pair.slice()), contiguous: fragments.length === 1, array: false, automatic: false};
            step.editor.syncSelector = () => {
                step.selector = selectorFromFragments(step.editor.fragments);
                step.text = transitionStepText(step.selector);
            };
        }
        return step.editor;
    }

    function setRange(item, lo, hi, domain, index) {
        if (item.kind === "default" || item.automatic) {
            return;
        }
        const at = Number.isInteger(index) ? index : 0;
        if (!item.fragments.length) {
            item.fragments.push([lo, hi]);
        }
        if (!item.fragments[at]) {
            return;
        }
        const bounds = domainBounds(domain);
        const left = clamp(Math.min(lo, hi), bounds.min, bounds.max);
        const right = clamp(Math.max(lo, hi), bounds.min, bounds.max);
        item.fragments[at] = [left, right];
        refreshItemBounds(item);
    }

    function setEdge(item, edge, value, domain, index) {
        const at = Number.isInteger(index) ? index : 0;
        if (item.kind === "default" || item.automatic || !item.fragments[at]) {
            return false;
        }
        const bounds = domainBounds(domain);
        let [lo, hi] = item.fragments[at];
        if (edge === "lo") {
            if (value > hi) {
                return false;
            }
            lo = clamp(value, bounds.min, hi);
        } else {
            if (value < lo) {
                return false;
            }
            hi = clamp(value, lo, bounds.max);
        }
        item.fragments[at] = [lo, hi];
        refreshItemBounds(item);
        return true;
    }

    function addBin(model, value) {
        const name = uniqueName(model);
        const item = {
            name,
            kind: "normal",
            lo: value,
            hi: value,
            fragments: [[value, value]],
            contiguous: true,
            array: false,
            arrayCount: null,
            automatic: false,
            original: {kind: "normal", name, selector: selectorFor(value, value)},
        };
        model.items.push(item);
        model.selected = name;
        model.selectedFragment = 0;
        model.armedEdge = "hi";
        syncDefault(model);
        return item;
    }

    function removeBin(model, name) {
        model.items = model.items.filter((item) => item.name !== name);
        if (model.selected === name) {
            model.selected = model.items[0]?.name || null;
            model.selectedFragment = 0;
            model.armedEdge = "hi";
        }
        syncDefault(model);
    }

    function renameBin(model, item, raw) {
        if (item.automatic) {
            return false;
        }
        const name = String(raw || "").trim();
        if (!name) {
            return false;
        }
        if (name === item.name) {
            return true;
        }
        if (model.items.some((other) => other !== item && other.name === name)) {
            return false;
        }
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
            return false;
        }
        if (model.selected === item.name) {
            model.selected = name;
        }
        item.name = name;
        if (item.original) {
            item.original = {...item.original, name};
        }
        return true;
    }

    function percent(model, value) {
        return ((value - model.min) / (model.max - model.min + 1)) * 100;
    }

    function widthPercent(model, lo, hi) {
        return ((hi - lo + 1) / (model.max - model.min + 1)) * 100;
    }

    function gridColumn(model, lo, hi) {
        return `${lo - model.min + 1} / ${hi - model.min + 2}`;
    }

    function valueFromClientX(axis, model, clientX) {
        const cells = axis.querySelectorAll(".bin-cell");
        if (cells.length) {
            for (const cell of cells) {
                const rect = cell.getBoundingClientRect();
                if (clientX < rect.right) {
                    return Number(cell.dataset.value);
                }
            }
            return model.max;
        }
        const track = axis.querySelector(".bin-lane") || axis;
        const rect = track.getBoundingClientRect();
        const ratio = rect.width ? (clientX - rect.left) / rect.width : 0;
        return clamp(model.min + Math.floor(ratio * (model.max - model.min + 1)), model.min, model.max);
    }

    function ticks(min, max) {
        const span = max - min;
        const rough = span / 5;
        const power = Math.pow(2, Math.round(Math.log2(Math.max(rough, 1))));
        const step = Math.max(1, power);
        const values = [min];
        let value = Math.ceil((min + 1) / step) * step;
        while (value < max) {
            values.push(value);
            value += step;
        }
        if (values[values.length - 1] !== max) {
            values.push(max);
        }
        return values;
    }

    function nudge(model, edge, delta) {
        const item = selectedItem(model);
        if (!item || item.kind === "default" || item.automatic) {
            return false;
        }
        const at = fragmentIndex(model, item);
        if (at < 0) {
            return false;
        }
        const [lo, hi] = item.fragments[at];
        if (edge === "lo" || edge === "hi") {
            const current = edge === "lo" ? lo : hi;
            return setEdge(item, edge, current + delta, model, at);
        }
        setRange(item, lo + delta, hi + delta, model, at);
        return true;
    }

    function isEnumModel(model) {
        const span = model.max - model.min + 1;
        if (span < 1 || !Object.keys(model.labels).length) {
            return false;
        }
        for (let value = model.min; value <= model.max; value += 1) {
            if (model.labels[value] == null) {
                return false;
            }
        }
        return true;
    }

    function enumValues(model) {
        const values = [];
        for (let value = model.min; value <= model.max; value += 1) {
            if (model.labels[value] != null) {
                values.push(value);
            }
        }
        return values;
    }

    function sizeNumber(input) {
        if (!input) {
            return;
        }
        const digits = Math.max(2, String(input.value).length);
        input.style.width = `calc(${digits}ch + 1.25em)`;
    }

    function sizeName(input) {
        if (!input) {
            return;
        }
        const length = Math.max(6, Array.from(input.value || "").length + 1);
        input.style.width = `calc(${length}ch + 1.25em)`;
    }

    function sizeCount(input) {
        if (!input) {
            return;
        }
        const length = Math.max(1, Array.from(input.value || "").length);
        input.style.width = `calc(${length}ch + .7em)`;
    }

    function kindSlot(selected) {
        if (selected.kind === "default" || selected.automatic) {
            return "";
        }
        const kinds = ["normal", "ignore", "illegal"];
        const index = Math.max(0, kinds.indexOf(selected.kind));
        const scope = Number.isInteger(selected.transitionBinIndex)
            ? ` data-transition-bin="${selected.transitionBinIndex}"`
            : "";
        return `<div class="bin-kind-slot" style="--kind:${index}" role="radiogroup" aria-label="bin 分类">${
            kinds.map((value) => {
                const on = selected.kind === value;
                return `<button type="button" class="bin-kind" data-kind="${value}"${scope} role="radio" aria-checked="${on}"${on ? " aria-pressed='true'" : ""}>${value}</button>`;
            }).join("")
        }</div>`;
    }

    function arrayControls(selected) {
        if (selected.kind === "default" || selected.automatic) {
            return "";
        }
        const mode = !selected.array ? "single" : selected.arrayCount === "auto" ? "auto" : "count";
        // Transition bins intentionally omit the explicit-count mode.  Do not
        // derive a count for their lightweight adapter: it has no fragments and
        // the value would never be rendered.
        const count = selected.allowArrayCount === false
            ? 0
            : Number.isInteger(selected.arrayCount) ? selected.arrayCount : suggestedArrayCount(selected);
        const scope = Number.isInteger(selected.transitionBinIndex)
            ? ` data-transition-bin="${selected.transitionBinIndex}"`
            : "";
        const choice = (value, label) => `<button type="button" class="bin-array-mode${mode === value ? " is-selected" : ""}" data-array-mode="${value}"${scope} role="radio" aria-checked="${mode === value}">${label}</button>`;
        const countChoice = selected.allowArrayCount === false ? "" : mode === "count"
            ? `<span class="bin-array-count-choice is-selected">${choice("count", "分为")}<input class="bin-array-count" value="${count}" spellcheck="false" inputmode="numeric" aria-label="数组分段数"><span>段</span></span>`
            : `<span class="bin-array-count-choice">${choice("count", "分为 x 段")}</span>`;
        return `<div class="bin-array-slot" role="radiogroup" aria-label="数组 bins 切分方式">
            ${choice("single", "单个")}
            ${choice("auto", "自动分段")}
            ${countChoice}
        </div>`;
    }

    function transitionBinEditorTarget(model, index) {
        const bin = model.transitionBins?.[index];
        if (!bin) return null;
        return {
            name: bin.name,
            kind: bin.coverageKind || "normal",
            array: Boolean(bin.array),
            arrayCount: bin.array ? "auto" : null,
            automatic: false,
            transitionBinIndex: index,
            allowArrayCount: false,
        };
    }

    function fragmentText(model, frag) {
        const [lo, hi] = frag;
        const value = (item) => (model.enum || isEnumModel(model)) ? (model.labels[item] ?? item) : item;
        return lo === hi ? String(value(lo)) : `[${value(lo)}:${value(hi)}]`;
    }

    function orderedFragments(item) {
        return item.fragments.map((fragment, index) => ({fragment, index})).sort((left, right) => (
            left.fragment[0] - right.fragment[0]
            || left.fragment[1] - right.fragment[1]
            || left.index - right.index
        ));
    }

    function suggestedArrayCount(item) {
        const values = mergeRanges(item.fragments).reduce((total, [lo, hi]) => total + hi - lo + 1, 0);
        if (values <= 1) {
            return 1;
        }
        return Math.min(values, Math.max(2, item.fragments.length));
    }

    function selectorSyntax(model, selected, editing = model.editingSelector) {
        return `<code class="bin-selector-syntax" data-selector="${esc(selected.name)}">{${orderedFragments(selected).map(({fragment: frag, index}) =>
            editing
                ? `<span class="bin-fragment-editor" data-fragment="${index}">${editableFragment(model, frag, index)}</span>`
                : `<button type="button" class="bin-fragment-token" data-fragment="${index}" title="双击编辑">${esc(fragmentText(model, frag))}</button>`
        ).join(", ")}}</code>`;
    }

    function editableFragment(model, frag, index) {
        const [lo, hi] = frag;
        if (model.enum || isEnumModel(model)) {
            const options = (selectedValue) => enumValues(model).map((value) =>
                `<option value="${value}"${value === selectedValue ? " selected" : ""}>${esc(model.labels[value])}</option>`
            ).join("");
            if (lo === hi) {
                return `<select class="bin-enum-value" data-fragment="${index}" aria-label="片选值">${options(lo)}</select>`;
            }
            return `[<select class="bin-enum-lo" data-fragment="${index}" aria-label="片选起始值">${options(lo)}</select>:<select class="bin-enum-hi" data-fragment="${index}" aria-label="片选结束值">${options(hi)}</select>]`;
        }
        if (lo === hi) {
            return `<input class="bin-value" type="number" data-fragment="${index}" aria-label="片选值">`;
        }
        return `[<input class="bin-lo" type="number" data-fragment="${index}" aria-label="片选起始值">:<input class="bin-hi" type="number" data-fragment="${index}" aria-label="片选结束值">]`;
    }

    function rangeRows(model, selected) {
        if (selected.kind === "default") {
            return `<div class="bin-editor-ranges"><div class="bin-editor-range"><span class="muted">覆盖所有尚未被其它 bins 覆盖的值。</span></div></div>`;
        }
        if (selected.automatic) {
            return `<div class="bin-editor-ranges"><div class="bin-editor-range"><span class="muted">由 option.auto_bin_max（${model.autoBinMax}）和当前值域自动生成，不可编辑。</span></div></div>`;
        }
        return "";
    }

    function paintCell(cell, model) {
        const value = Number(cell.dataset.value);
        const hits = occupants(model, value);
        const selected = selectedItem(model);
        const transitionStep = model.transitionActive && model.transitionBins?.[model.transitionActive.bin]?.steps[model.transitionActive.step];
        const transitionRanges = transitionStep?.editor?.fragments
            || rangesFromSelector(transitionStep?.selector);
        cell.className = "bin-cell";
        cell.removeAttribute("title");
        cell.style.removeProperty("--cell");
        if (transitionRanges?.some(([lo, hi]) => value >= lo && value <= hi)) {
            cell.classList.add("is-transition", "is-selected-range");
            cell.style.setProperty("--cell", "#7c3aed");
            return;
        }
        if (selected && covers(selected, value)) {
            const color = model.transitionActive ? "#7c3aed" : colorFor(selected, itemIndex(model, selected));
            cell.classList.add(`is-${selected.kind}`, "is-selected-range");
            cell.style.setProperty("--cell", color);
            if (hits.length > 1) {
                cell.classList.add("is-overlap");
            }
            return;
        }
        if (!hits.length) {
            const leftover = defaultItem(model);
            if (leftover && leftover.fragments.some(([lo, hi]) => value >= lo && value <= hi)) {
                cell.classList.add("is-default", "is-addable");
                return;
            }
            cell.classList.add("is-empty");
            return;
        }
        const owner = cellOwner(model, value);
        const color = colorFor(owner, itemIndex(model, owner));
        cell.classList.add(`is-${owner.kind}`, "is-idle");
        cell.style.setProperty("--cell", color);
        if (owner.automatic) {
            cell.classList.add("is-automatic");
        }
        if (hits.length > 1) {
            cell.classList.add("is-overlap");
        }
    }

    function clipMarkup(model, item, colorIndex, lo, hi, compact, fragment) {
        const editable = item.kind !== "default" && !item.automatic ? " is-editable" : "";
        const leftover = item.kind === "default" ? " is-default" : item.automatic ? " is-automatic" : "";
        const active = item.name === model.selected && fragment === model.selectedFragment ? " is-selected" : "";
        const loArmed = active && model.armedEdge === "lo" ? " is-armed" : "";
        const hiArmed = active && model.armedEdge === "hi" ? " is-armed" : "";
        const place = compact
            ? `grid-column:${gridColumn(model, lo, hi)}`
            : `left:${percent(model, lo)}%;width:${widthPercent(model, lo, hi)}%`;
        return `<div class="bin-clip${editable}${leftover}${active}" data-bin="${esc(item.name)}" data-fragment="${fragment}" data-lo="${lo}" data-hi="${hi}"
            style="${place};background:${colorFor(item, colorIndex)}">
            <span class="bin-handle bin-handle-lo${loArmed}" data-edge="lo"></span>
            <span class="bin-clip-label"></span>
            <span class="bin-handle bin-handle-hi${hiArmed}" data-edge="hi"></span>
        </div>`;
    }

    function laneMarkup(model, item, colorIndex, compact) {
        const tracks = packTracks(visibleFragmentItems(model, item));
        const trackHtml = (tracks.length ? tracks : [[]]).map((track) => {
            const clips = track.map((piece) => clipMarkup(model, item, colorIndex, piece.lo, piece.hi, compact, piece.index)).join("");
            return `<div class="bin-track">${clips}</div>`;
        }).join("");
        const handle = item.automatic ? "" : `<button type="button" class="bin-row-handle" draggable="true" title="拖动调整 bin 顺序" aria-label="拖动调整 ${esc(item.name)} 顺序">⠿</button>`;
        const remove = item.automatic ? "" : `<button type="button" class="bin-row-delete" title="删除 bin" aria-label="删除 ${esc(item.name)}">×</button>`;
        return `<div class="bin-row${item.automatic ? "" : " has-delete"}" data-bin="${esc(item.name)}">
            <span class="bin-row-name" title="${esc(item.name)}">${handle}<span class="bin-row-name-text">${esc(item.name)}</span></span>
            ${remove}
            <div class="bin-lane" data-bin="${esc(item.name)}" data-kind="${esc(item.kind)}">${trackHtml}</div>
        </div>`;
    }

    function transitionLaneMarkup(model, bin, index) {
        const active = model.transitionActive?.bin === index;
        const selected = model.selectedTransition === index;
        const step = active ? bin.steps[model.transitionActive.step] : null;
        const editor = active ? selectedItem(model) : null;
        const compact = usesCells(model);
        const detail = editor ? `<div class="transition-active-track bin-track">${editor.fragments.map(([lo, hi], fragment) => clipMarkup(model, editor, index, lo, hi, compact, fragment)).join("")}</div>` : "";
        const steps = bin.steps.map((entry, stepIndex) => {
            const isActive = active && model.transitionActive.step === stepIndex;
            const selector = selectorSyntax(model, transitionSelectorItem(model, index, stepIndex), isActive && model.editingSelector);
            return `<span class="transition-step-selector${isActive ? " is-selected" : ""}" data-transition-step="${stepIndex}">${selector}${entry.repeat ? ` [*${entry.minimum}:${entry.maximum}]` : ""}</span>${stepIndex + 1 < bin.steps.length ? '<span class="transition-arrow">→</span>' : ""}`;
        }).join("");
        const handle = `<button type="button" class="bin-row-handle" draggable="true" title="拖动调整 bin 顺序" aria-label="拖动调整 ${esc(bin.name)} 顺序">⠿</button>`;
        const remove = `<button type="button" class="bin-row-delete" title="删除 bin" aria-label="删除 ${esc(bin.name)}">×</button>`;
        return `<div class="bin-row transition-bin-row has-delete${active ? " is-active" : ""}${selected ? " is-selected" : ""}" data-transition-bin="${index}" data-row-kind="transition">
            <span class="bin-row-name" title="${esc(bin.name)}">${handle}<span class="bin-row-name-text">${esc(bin.name)}</span></span>
            ${remove}
            <div class="bin-lane transition-lane">${steps}</div>
            ${detail}
        </div>`;
    }

    function syncBinsPanel(host, model) {
        const panel = host.closest(".bin-canvas-panel")?.parentElement?.querySelector(".bins-panel .panel-body");
        if (!panel) {
            return;
        }
        const rows = model.items.map((item) => {
            const selector = item.kind === "default"
                ? "default"
                : item.automatic
                    ? `auto (${orderedFragments(item).map(({fragment}) => fragmentText(model, fragment)).join(", ")})`
                    : `{${orderedFragments(item).map(({fragment}) => fragmentText(model, fragment)).join(", ")}}`;
            return `<tr><td class="bin-name">${esc(item.name)}</td><td>${esc(item.kind)}</td><td><code>${esc(selector)}</code></td></tr>`;
        }).join("") + (model.transitionBins || []).map((bin) => `<tr><td class="bin-name">${esc(bin.name)}${bin.array ? "[]" : ""}</td><td>${esc(bin.coverageKind || "normal")} transition</td><td><code>${esc(transitionSyntax(bin))}</code></td></tr>`).join("");
        panel.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Bin</th><th>分类</th><th>值 / 选择器</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function dslSelector(model, item) {
        const value = (itemValue) => (model.enum || isEnumModel(model))
            ? (model.labels[itemValue] ?? itemValue)
            : itemValue;
        return orderedFragments(item).map(({fragment: [lo, hi]}) => (
            lo === hi ? String(value(lo)) : `${value(lo)}:${value(hi)}`
        )).join(", ");
    }

    function dslBinLine(model, item) {
        if (item.kind === "default") {
            return `${item.name} = default_bins`;
        }
        const family = item.kind === "ignore" ? "ignore_bins" : item.kind === "illegal" ? "illegal_bins" : "bins";
        return `${item.name} = ${family}[${dslSelector(model, item)}]`;
    }

    function dslTransitionLine(model, bin) {
        const steps = bin.steps.map((step) => {
            const item = transitionSelectorItem(model, model.transitionBins.indexOf(bin), bin.steps.indexOf(step));
            const selector = item ? dslSelector(model, item) : transitionStepText(step.selector);
            return step.repeat ? `(${selector})[*${step.minimum}:${step.maximum}]` : selector;
        }).join(" => ");
        const family = bin.coverageKind === "ignore" ? "ignore_bins" : bin.coverageKind === "illegal" ? "illegal_bins" : "bins";
        return `${bin.name}${bin.array ? "[]" : ""} = ${family}[${steps}]`;
    }

    function dslExpr(value) {
        if (!value || typeof value !== "object") {
            return value == null ? "..." : String(value);
        }
        if (value.kind === "field" || value.kind === "container_values" || value.kind === "assoc_values") return value.path;
        if (value.kind === "name" || value.kind === "parameter_ref") return value.name;
        if (value.kind === "attribute") return `${dslExpr(value.base)}.${value.name}`;
        if (value.kind === "subscript") return `${dslExpr(value.base)}[${dslExpr(value.index)}]`;
        if (value.kind === "constant") return typeof value.value === "string" ? JSON.stringify(value.value) : String(value.value);
        if (value.kind === "binary") return `${dslExpr(value.left)} ${value.operator} ${dslExpr(value.right)}`;
        if (value.kind === "compare") return `${dslExpr(value.left)} ${value.operators.map((op, index) => `${op} ${dslExpr(value.comparators[index])}`).join(" ")}`;
        return "...";
    }

    function highlightDsl(text) {
        const token = /(\b[A-Za-z_][A-Za-z0-9_]*\b|\b\d+\b|=>|==|[()[\],.:=])/g;
        let className = false;
        let member = false;
        return String(text).split(token).map((part) => {
            if (!part) return "";
            if (part === "class") {
                className = true;
                return `<span class="dsl-keyword">${esc(part)}</span>`;
            }
            if (className && /^[A-Za-z_]/.test(part)) {
                className = false;
                return `<span class="dsl-class-name">${esc(part)}</span>`;
            }
            if (part === "CovPoint" || part === "Cross") return `<span class="dsl-type">${esc(part)}</span>`;
            if (part === "source" || part === "iff") return `<span class="dsl-argument">${esc(part)}</span>`;
            if (part === "lambda" || part === "pass") return `<span class="dsl-keyword">${esc(part)}</span>`;
            if (part === "self") return `<span class="dsl-self">${esc(part)}</span>`;
            if (member && /^[A-Za-z_]/.test(part)) {
                member = false;
                return `<span class="dsl-member">${esc(part)}</span>`;
            }
            if (/^(bins|ignore_bins|illegal_bins|transition_bins|default_bins)$/.test(part)) return `<span class="dsl-bin-family">${esc(part)}</span>`;
            if (/^\d+$/.test(part)) return `<span class="dsl-number">${esc(part)}</span>`;
            if (part === ".") {
                member = true;
                return `<span class="dsl-punctuation">${esc(part)}</span>`;
            }
            if (/^(=>|==|=|:)$/.test(part)) return `<span class="dsl-operator">${esc(part)}</span>`;
            if (/^[()[\],]$/.test(part)) return `<span class="dsl-punctuation">${esc(part)}</span>`;
            return esc(part);
        }).join("");
    }

    function pointDsl(model, point) {
        const source = dslExpr(point?.expression);
        const iff = point?.iff ? `, iff=${dslExpr(point.iff)}` : "";
        const lines = [`class ${point?.name || "coverpoint"}(CovPoint, source=${source}${iff}):`];
        lines.push(...model.items.filter((item) => !item.automatic).map((item) => `    ${dslBinLine(model, item)}`));
        lines.push(...(model.transitionBins || []).map((bin) => `    ${dslTransitionLine(model, bin)}`));
        if (lines.length === 1) {
            lines.push("    pass");
        }
        return lines.join("\n");
    }

    function syncDslCard(host, model) {
        const card = host.closest(".bin-canvas-panel")?.parentElement?.querySelector(".dsl-card[data-dsl-point]");
        if (card && host._dslPoint) {
            card.querySelector("code").innerHTML = highlightDsl(pointDsl(model, host._dslPoint));
        }
    }

    function installPointDslCard(host, point) {
        const canvasPanel = host.closest(".bin-canvas-panel");
        if (!canvasPanel || !point) {
            return;
        }
        host._dslPoint = point;
        host._sourceDsl = typeof point.source === "string" ? point.source : null;
        let card = canvasPanel.parentElement?.querySelector(".dsl-card[data-dsl-point]");
        if (!card) {
            card = document.createElement("details");
            card.className = "panel dsl-card";
            card.dataset.dslPoint = "true";
            card.open = true;
            card.innerHTML = "<summary class='panel-head'><h3>DSL</h3><span class='muted'>当前草稿</span></summary><div class='panel-body'><pre class='definition'><code></code></pre></div>";
            canvasPanel.insertAdjacentElement("afterend", card);
        }
        const model = host._binModel;
        if (model) {
            card.querySelector("code").innerHTML = highlightDsl(host._sourceDsl || pointDsl(model, point));
        }
    }

    function render(host, model, original) {
        host._binModel = model;
        host._binOriginal = original;
        syncAutomatic(model);
        syncDefault(model);
        model.extraBins = (model.transitionBins || []).map((bin) => ({
            name: bin.name,
            kind: bin.kind || "transition",
            selector: transitionSyntax(bin),
        }));
        recordHistory(host, model);
        const count = model.max - model.min + 1;
        const selected = selectedItem(model);
        const compact = usesCells(model);
        const lanes = model.items.map((item, index) => laneMarkup(model, item, index, compact)).join("")
            + (model.transitionBins || []).map((bin, index) => transitionLaneMarkup(model, bin, index)).join("");

        let scale = "";
        if (compact) {
            const cells = [];
            for (let value = model.min; value <= model.max; value += 1) {
                const label = model.labels[value] ?? value;
                cells.push(`<button type="button" class="bin-cell" data-value="${value}">${esc(label)}</button>`);
            }
            scale = `<div class="bin-scale-row"><div class="bin-new-actions"><button type="button" class="bin-new-icon bin-add" title="新建 bin" aria-label="新建 bin">＋</button><button type="button" class="bin-new-icon bin-add-default" title="新建 default" aria-label="新建 default"${defaultItem(model) ? " disabled" : ""}>◇</button><button type="button" class="bin-new-icon bin-add-transition" title="新建 transition bin" aria-label="新建 transition bin">⇢</button></div><div class="bin-cells">${cells.join("")}</div></div>`;
        } else {
            scale = `<div class="bin-scale-row"><div class="bin-new-actions"><button type="button" class="bin-new-icon bin-add" title="新建 bin" aria-label="新建 bin">＋</button><button type="button" class="bin-new-icon bin-add-default" title="新建 default" aria-label="新建 default"${defaultItem(model) ? " disabled" : ""}>◇</button><button type="button" class="bin-new-icon bin-add-transition" title="新建 transition bin" aria-label="新建 transition bin">⇢</button></div><div class="bin-ticks">${ticks(model.min, model.max).map((value) =>
                `<span style="left:${percent(model, value)}%">${esc(value)}</span>`
            ).join("")}</div></div>`;
        }

        const nameControl = selected?.automatic
            ? `<span class="bin-name-text" title="自动 bins">auto</span>`
            : selected
                ? `<input class="bin-name-input" value="${esc(selected.name)}" spellcheck="false" aria-label="bin 名称">`
                : "";
        const selectorControl = selected && selected.kind !== "default" && !selected.automatic
            ? `<span class="bin-selector-control"><span class="bin-editor-divider" aria-hidden="true">·</span><span class="bin-selector-label">选择器</span>${selectorSyntax(model, selected)}</span>`
            : "";
        const transitionIndex = Number.isInteger(model.selectedTransition)
            ? model.selectedTransition
            : null;
        const transitionBin = transitionIndex == null
            ? null
            : model.transitionBins?.[transitionIndex];
        const transitionTarget = transitionBin
            ? transitionBinEditorTarget(model, transitionIndex)
            : null;
        const transitionEditor = transitionBin ? `<div class="bin-editor-main transition-bin-editor">
                <span class="bin-swatch" style="background:#7c3aed"></span>
                <input class="bin-name-input" data-transition-bin="${transitionIndex}" value="${esc(transitionBin.name)}" spellcheck="false" aria-label="transition bin 名称">
                ${arrayControls(transitionTarget)}
                ${kindSlot(transitionTarget)}
            </div>` : "";
        const selectedEditor = transitionBin ? transitionEditor : (selected ? `<div class="bin-editor-main">
                <span class="bin-swatch" style="background:${colorFor(selected, itemIndex(model, selected))}"></span>
                ${nameControl}
                ${arrayControls(selected)}
                ${kindSlot(selected)}
                ${selectorControl}
            </div>
            ${rangeRows(model, selected)}` : `<p class="muted">先点选一个 bin，选中后再拖动或改边沿。</p>`);

        host.innerHTML = `<div class="bin-canvas${compact ? "" : " is-wide"}" style="--cells:${count}">
            <div class="bin-axis" tabindex="0">
                <div class="bin-lanes">${lanes}</div>
                ${scale}
            </div>
            <div class="bin-editor">
                ${selectedEditor}
            </div>
        </div>`;
        host.querySelectorAll(".bin-cell").forEach((cell) => paintCell(cell, model));
        host.querySelectorAll(".transition-bin-row").forEach((row) => {
            row.querySelectorAll(".transition-step-selector").forEach((chip) => {
                const bin = Number(row.dataset.transitionBin);
                const step = Number(chip.dataset.transitionStep);
                const activate = (editing, fragment = 0) => {
                    model.selectedTransition = bin;
                    model.transitionActive = {bin, step};
                    const item = selectedItem(model);
                    model.selected = item?.name || null;
                    if (editing && item) {
                        model.selectedFragment = fragment;
                        model.editingFragment = fragment;
                        model.selectorEditSnapshot = item.fragments.map((pair) => pair.slice());
                        model.editingSelectorFocusPending = true;
                    }
                    model.editingSelector = editing;
                    render(host, model, original);
                    if (editing) window.requestAnimationFrame(() => {
                        host.querySelector(`.transition-step-selector.is-selected .bin-fragment-editor[data-fragment="${fragment}"] input, .transition-step-selector.is-selected .bin-fragment-editor[data-fragment="${fragment}"] select`)?.focus();
                        model.editingSelectorFocusPending = false;
                    });
                };
                let previousTokenPress = null;
                chip.querySelectorAll(".bin-fragment-token").forEach((token) => {
                    token.addEventListener("pointerdown", (event) => {
                        if (event.button !== 0) return;
                        const fragment = Number(token.dataset.fragment);
                        const sameToken = previousTokenPress?.token === token
                            && event.timeStamp - previousTokenPress.time <= 900
                            && Math.abs(event.clientX - previousTokenPress.x) <= 8
                            && Math.abs(event.clientY - previousTokenPress.y) <= 8;
                        if (!sameToken) {
                            previousTokenPress = {token, time: event.timeStamp, x: event.clientX, y: event.clientY};
                            return;
                        }
                        previousTokenPress = null;
                        event.preventDefault();
                        event.stopPropagation();
                        activate(true, Number.isInteger(fragment) ? fragment : 0);
                    });
                });
                chip.addEventListener("click", (event) => {
                    // Token presses own their double-click editing lifecycle.
                    // A click in the remaining selector area merely selects it.
                    if (event.target.closest("input, select, .bin-fragment-token")) return;
                    activate(false);
                });
            });
            row.addEventListener("click", (event) => {
                if (event.target.closest(".bin-row-handle, .bin-row-delete, .transition-step-selector")) {
                    return;
                }
                const index = Number(row.dataset.transitionBin);
                if (!Number.isInteger(index) || model.selectedTransition === index) {
                    return;
                }
                model.selectedTransition = index;
                model.transitionActive = null;
                model.editingSelector = false;
                render(host, model, original);
            });
        });
        host.querySelectorAll(".bin-lo").forEach((input) => {
            const item = selectedItem(model);
            const at = Number(input.dataset.fragment);
            if (!item?.fragments[at]) {
                return;
            }
            input.min = String(model.min);
            input.max = String(model.max);
            input.value = String(item.fragments[at][0]);
            sizeNumber(input);
        });
        host.querySelectorAll(".bin-hi").forEach((input) => {
            const item = selectedItem(model);
            const at = Number(input.dataset.fragment);
            if (!item?.fragments[at]) {
                return;
            }
            input.min = String(model.min);
            input.max = String(model.max);
            input.value = String(item.fragments[at][1]);
            sizeNumber(input);
        });
        host.querySelectorAll(".bin-value").forEach((input) => {
            const item = selectedItem(model);
            const at = Number(input.dataset.fragment);
            if (!item?.fragments[at]) {
                return;
            }
            input.min = String(model.min);
            input.max = String(model.max);
            input.value = String(item.fragments[at][0]);
            sizeNumber(input);
        });
        sizeName(host.querySelector(".bin-name-input"));
        sizeCount(host.querySelector(".bin-array-count"));
        bind(host, model, original);
        persist(host, model);
        wireChrome(host, model, original);
        syncBinsPanel(host, model);
        syncDslCard(host, model);
    }

    function paintLive(host, model) {
        const item = selectedItem(model);
        if (!item) {
            return;
        }
        host.querySelectorAll(`.bin-clip[data-bin="${CSS.escape(item.name)}"]`).forEach((clip) => {
            const index = Number(clip.dataset.fragment);
            const frag = item.fragments[index];
            if (!frag) {
                return;
            }
            const [lo, hi] = frag;
            if (usesCells(model)) {
                clip.style.gridColumn = gridColumn(model, lo, hi);
                clip.style.removeProperty("left");
                clip.style.removeProperty("width");
            } else {
                clip.style.left = `${percent(model, lo)}%`;
                clip.style.width = `${widthPercent(model, lo, hi)}%`;
            }
            clip.dataset.lo = String(lo);
            clip.dataset.hi = String(hi);
        });
        host.querySelectorAll(".bin-lo").forEach((input) => {
            const index = Number(input.dataset.fragment);
            if (item.fragments[index]) {
                input.value = String(item.fragments[index][0]);
                sizeNumber(input);
            }
        });
        host.querySelectorAll(".bin-hi").forEach((input) => {
            const index = Number(input.dataset.fragment);
            if (item.fragments[index]) {
                input.value = String(item.fragments[index][1]);
                sizeNumber(input);
            }
        });
        host.querySelectorAll(".bin-value").forEach((input) => {
            const index = Number(input.dataset.fragment);
            if (item.fragments[index]) {
                input.value = String(item.fragments[index][0]);
                sizeNumber(input);
            }
        });
        host.querySelectorAll(`.bin-selector-syntax[data-selector="${CSS.escape(item.name)}"] .bin-fragment-token`).forEach((token) => {
            const index = Number(token.dataset.fragment);
            if (item.fragments[index]) {
                token.textContent = fragmentText(model, item.fragments[index]);
            }
        });
        host.querySelectorAll(".bin-cell").forEach((cell) => paintCell(cell, model));
        syncBinsPanel(host, model);
        syncDslCard(host, model);
        persist(host, model);
    }

    function persist(host, model) {
        const key = host.dataset.canvasKey;
        if (key) {
            drafts.set(key, snapshotModel(model));
        }
    }

    function restore(host, model) {
        const saved = drafts.get(host.dataset.canvasKey);
        if (!saved?.items) {
            return;
        }
        applySnapshot(model, saved);
    }

    function syncDomainChrome(panel, model) {
        if (!panel) {
            return;
        }
        panel.querySelectorAll(".bin-domain-bound").forEach((bound) => {
            bound.classList.toggle("hidden", Boolean(model.enum));
        });
        const minInput = panel.querySelector(".bin-domain-min");
        const maxInput = panel.querySelector(".bin-domain-max");
        if (minInput) {
            minInput.min = String(model.nativeMin);
            minInput.max = String(model.nativeMax);
            minInput.value = String(model.min);
            minInput.disabled = Boolean(model.enum);
            sizeNumber(minInput);
        }
        if (maxInput) {
            maxInput.min = String(model.nativeMin);
            maxInput.max = String(model.nativeMax);
            maxInput.value = String(model.max);
            maxInput.disabled = Boolean(model.enum);
            sizeNumber(maxInput);
        }
        const stats = panel.querySelector(".bin-domain-stats");
        if (stats) {
            stats.textContent = statsMarkup(model);
        }
    }

    function syncHistoryChrome(panel, host) {
        const history = historyFor(host);
        const undoButton = panel.querySelector(".bin-undo");
        const redoButton = panel.querySelector(".bin-redo");
        if (undoButton) {
            undoButton.disabled = !history?.undo.length;
        }
        if (redoButton) {
            redoButton.disabled = !history?.redo.length;
        }
    }

    function wireChrome(host, model, original) {
        const panel = host.closest(".bin-canvas-panel");
        if (!panel) {
            return;
        }
        syncDomainChrome(panel, model);
        syncHistoryChrome(panel, host);
        if (panel.dataset.binChromeWired === "true") {
            return;
        }
        panel.dataset.binChromeWired = "true";
        const reset = panel.querySelector(".bin-reset");
        const undoButton = panel.querySelector(".bin-undo");
        const redoButton = panel.querySelector(".bin-redo");
        const help = panel.querySelector(".bin-help");
        const helpSummary = help?.querySelector("summary");
        const minInput = panel.querySelector(".bin-domain-min");
        const maxInput = panel.querySelector(".bin-domain-max");
        reset?.addEventListener("click", () => {
            const current = host._binModel;
            const axis = axisSpec(current);
            drafts.delete(host.dataset.canvasKey);
            histories.delete(host.dataset.canvasKey);
            const fresh = modelFromBins(original, axis);
            if (fresh) {
                fresh.transitionBins = transitionModelFromBins(original).bins;
                fresh.hasTransitionBins = fresh.transitionBins.length > 0;
                fresh.extraBins = fresh.transitionBins.map((bin) => ({
                    name: bin.name,
                    kind: bin.kind || "transition",
                    selector: transitionSyntax(bin),
                }));
                render(host, fresh, original);
            }
        });
        undoButton?.addEventListener("click", () => undo(host, host._binModel, host._binOriginal));
        redoButton?.addEventListener("click", () => redo(host, host._binModel, host._binOriginal));
        helpSummary?.addEventListener("mouseleave", () => {
            if (help) {
                help.open = false;
            }
        });
        const applyAxis = () => {
            const current = host._binModel;
            if (current.enum) {
                syncDomainChrome(panel, current);
                return;
            }
            const min = Number(minInput?.value);
            const max = Number(maxInput?.value);
            if (!applyDomain(current, min, max)) {
                syncDomainChrome(panel, current);
                return;
            }
            render(host, current, original);
        };
        minInput?.addEventListener("change", applyAxis);
        maxInput?.addEventListener("change", applyAxis);
        const nudgeDomain = (input, delta) => {
            const current = host._binModel;
            if (!input || current.enum) {
                return;
            }
            input.value = String(Number(input.value) + delta);
            applyAxis();
        };
        minInput?.addEventListener("wheel", (event) => {
            event.preventDefault();
            nudgeDomain(minInput, event.deltaY < 0 ? 1 : -1);
        }, {passive: false});
        maxInput?.addEventListener("wheel", (event) => {
            event.preventDefault();
            nudgeDomain(maxInput, event.deltaY < 0 ? 1 : -1);
        }, {passive: false});
        minInput?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                minInput.blur();
            }
        });
        maxInput?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                maxInput.blur();
            }
        });
    }

    function bind(host, model, original) {
        const axis = host.querySelector(".bin-axis");
        const canvas = host.querySelector(".bin-canvas");

        const nameInput = host.querySelector(".bin-name-input");
        nameInput?.addEventListener("input", () => sizeName(nameInput));
        nameInput?.addEventListener("change", () => {
            const transitionIndex = Number(nameInput.dataset.transitionBin);
            if (Number.isInteger(transitionIndex)) {
                const bin = model.transitionBins?.[transitionIndex];
                const name = nameInput.value.trim();
                const duplicate = model.items.some((item) => item.name === name)
                    || model.transitionBins.some((item) => item !== bin && item.name === name);
                if (!bin || !name || duplicate) {
                    if (bin) nameInput.value = bin.name;
                    sizeName(nameInput);
                    return;
                }
                bin.name = name;
                render(host, model, original);
                return;
            }
            const item = selectedItem(model);
            if (!item || !renameBin(model, item, nameInput.value)) {
                if (item) {
                    nameInput.value = item.name;
                    sizeName(nameInput);
                }
                return;
            }
            render(host, model, original);
        });
        nameInput?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                nameInput.blur();
            }
        });

        const setArrayMode = (mode) => {
            const item = selectedItem(model);
            if (!item || item.automatic) {
                return;
            }
            if (mode === "single") {
                item.array = false;
                item.arrayCount = null;
            } else if (mode === "auto") {
                item.array = true;
                item.arrayCount = "auto";
            } else {
                item.array = true;
                if (!Number.isInteger(item.arrayCount)) {
                    item.arrayCount = suggestedArrayCount(item);
                }
            }
            render(host, model, original);
        };
        host.querySelectorAll(".bin-array-mode").forEach((button) => {
            button.addEventListener("click", (event) => {
                event.stopPropagation();
                const transitionIndex = Number(button.dataset.transitionBin);
                if (Number.isInteger(transitionIndex)) {
                    const bin = model.transitionBins?.[transitionIndex];
                    if (!bin) return;
                    bin.array = button.dataset.arrayMode === "auto";
                    render(host, model, original);
                    return;
                }
                setArrayMode(button.dataset.arrayMode);
            });
        });
        host.querySelector(".bin-array-count-choice")?.addEventListener("click", (event) => {
            if (event.target.closest("input")) {
                return;
            }
            setArrayMode("count");
        });
        const countInput = host.querySelector(".bin-array-count");
        countInput?.addEventListener("input", () => sizeCount(countInput));
        countInput?.addEventListener("change", () => {
            const item = selectedItem(model);
            const count = parseArrayCount(countInput.value);
            if (!item || count == null) {
                if (item?.array && item.arrayCount !== "auto") {
                    countInput.value = String(item.arrayCount ?? 2);
                    sizeCount(countInput);
                }
                return;
            }
            item.array = true;
            item.arrayCount = count;
            render(host, model, original);
        });
        countInput?.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                countInput.blur();
            }
        });
        countInput?.addEventListener("wheel", (event) => {
            event.preventDefault();
            const item = selectedItem(model);
            const count = Number(countInput.value);
            if (!item || !Number.isInteger(count)) {
                return;
            }
            item.array = true;
            item.arrayCount = Math.max(1, count + (event.deltaY < 0 ? 1 : -1));
            render(host, model, original);
        }, {passive: false});

        host.querySelectorAll(".bin-row-delete").forEach((button) => {
            button.addEventListener("click", (event) => {
                event.stopPropagation();
                const row = button.closest(".bin-row");
                const transitionIndex = Number(row?.dataset.transitionBin);
                if (Number.isInteger(transitionIndex)) {
                    const selectedBin = model.transitionBins?.[model.selectedTransition];
                    const activeBin = model.transitionActive && model.transitionBins?.[model.transitionActive.bin];
                    const activeStep = model.transitionActive?.step;
                    model.transitionBins.splice(transitionIndex, 1);
                    model.selectedTransition = selectedBin ? model.transitionBins.indexOf(selectedBin) : null;
                    model.transitionActive = activeBin && model.transitionBins.includes(activeBin)
                        ? {bin: model.transitionBins.indexOf(activeBin), step: activeStep}
                        : null;
                    render(host, model, original);
                    return;
                }
                const name = row?.dataset.bin;
                if (!name) {
                    return;
                }
                removeBin(model, name);
                render(host, model, original);
            });
        });
        let draggedRow = null;
        host.querySelectorAll(".bin-row-handle").forEach((handle) => {
            handle.addEventListener("dragstart", (event) => {
                const row = handle.closest(".bin-row");
                const transitionIndex = Number(row?.dataset.transitionBin);
                draggedRow = Number.isInteger(transitionIndex)
                    ? {kind: "transition", index: transitionIndex}
                    : row?.dataset.bin ? {kind: "bin", name: row.dataset.bin} : null;
                event.dataTransfer?.setData("text/plain", draggedRow ? JSON.stringify(draggedRow) : "");
                if (event.dataTransfer) {
                    event.dataTransfer.effectAllowed = "move";
                }
            });
            handle.addEventListener("dragend", () => {
                draggedRow = null;
                host.querySelectorAll(".bin-row.is-drop-target").forEach((row) => row.classList.remove("is-drop-target"));
            });
        });
        host.querySelectorAll(".bin-row").forEach((row) => {
            row.addEventListener("pointerdown", (event) => {
                if (event.button !== 0 || event.target.closest(".bin-row-handle, .bin-row-delete, .bin-lane")) {
                    return;
                }
                const name = row.dataset.bin;
                if (!name || name === model.selected) {
                    return;
                }
                model.selected = name;
                model.selectedFragment = 0;
                model.editingFragment = null;
                render(host, model, original);
            });
            row.addEventListener("dragover", (event) => {
                const transitionIndex = Number(row.dataset.transitionBin);
                const target = Number.isInteger(transitionIndex)
                    ? {kind: "transition", index: transitionIndex}
                    : row.dataset.bin ? {kind: "bin", name: row.dataset.bin} : null;
                if (!draggedRow || !target || draggedRow.kind !== target.kind
                    || (draggedRow.kind === "transition" && draggedRow.index === target.index)
                    || (draggedRow.kind === "bin" && draggedRow.name === target.name)) {
                    return;
                }
                event.preventDefault();
                row.classList.add("is-drop-target");
            });
            row.addEventListener("dragleave", () => row.classList.remove("is-drop-target"));
            row.addEventListener("drop", (event) => {
                event.preventDefault();
                row.classList.remove("is-drop-target");
                const transitionIndex = Number(row.dataset.transitionBin);
                if (draggedRow?.kind === "transition" && Number.isInteger(transitionIndex)) {
                    const selectedBin = model.transitionBins?.[model.selectedTransition];
                    const activeBin = model.transitionActive && model.transitionBins?.[model.transitionActive.bin];
                    const activeStep = model.transitionActive?.step;
                    const [bin] = model.transitionBins.splice(draggedRow.index, 1);
                    model.transitionBins.splice(transitionIndex, 0, bin);
                    model.selectedTransition = selectedBin ? model.transitionBins.indexOf(selectedBin) : null;
                    model.transitionActive = activeBin && model.transitionBins.includes(activeBin)
                        ? {bin: model.transitionBins.indexOf(activeBin), step: activeStep}
                        : null;
                    render(host, model, original);
                    return;
                }
                const targetName = row.dataset.bin;
                if (draggedRow?.kind !== "bin" || !targetName || draggedRow.name === targetName) {
                    return;
                }
                const source = model.items.findIndex((item) => item.name === draggedRow.name);
                const target = model.items.findIndex((item) => item.name === targetName);
                if (source < 0 || target < 0) {
                    return;
                }
                const [item] = model.items.splice(source, 1);
                model.items.splice(target, 0, item);
                render(host, model, original);
            });
        });

        host.querySelectorAll(".bin-clip").forEach((node) => {
            node.addEventListener("pointerdown", (event) => {
                if (event.target.closest(".bin-handle")) {
                    return;
                }
                const fragment = Number(node.dataset.fragment);
                const item = node.closest(".transition-active-track")
                    ? selectedItem(model)
                    : model.items.find((entry) => entry.name === node.dataset.bin);
                if (item && item.kind !== "default" && !item.automatic && event.button === 0) {
                    model.selected = item.name;
                    model.selectedFragment = fragment;
                    model.editingFragment = null;
                    if (event.shiftKey) {
                        startDeleteDrag(event, host, model, original, node, item, fragment);
                    } else {
                        startDrag(event, host, model, original, axis, "move", item, fragment);
                    }
                    return;
                }
                if (item) {
                    model.selected = item.name;
                    model.selectedFragment = fragment;
                    model.editingFragment = null;
                    render(host, model, original);
                }
                axis.focus({preventScroll: true});
            });
        });

        host.querySelectorAll(".bin-lane").forEach((lane) => {
            let selectTimer = 0;
            let previousPress = null;
            const flushSelect = () => {
                selectTimer = 0;
                render(host, model, original);
            };
            lane.addEventListener("pointerdown", (event) => {
                if (event.target.closest(".bin-clip")) {
                    return;
                }
                const item = model.items.find((entry) => entry.name === lane.dataset.bin);
                if (!item) {
                    return;
                }
                const isSecondPress = event.button === 0
                    && previousPress
                    && event.timeStamp - previousPress.time <= 500
                    && Math.abs(event.clientX - previousPress.x) <= 8;
                if (isSecondPress && item.kind !== "default" && !item.automatic) {
                    previousPress = null;
                    window.clearTimeout(selectTimer);
                    selectTimer = 0;
                    const value = valueFromClientX(axis, model, event.clientX);
                    const fragment = addFragment(item, value, value, model);
                    model.selected = item.name;
                    model.selectedFragment = fragment;
                    model.editingFragment = null;
                    model.armedEdge = "hi";
                    render(host, model, original);
                    const nextAxis = host.querySelector(".bin-axis");
                    startDrag(event, host, model, original, nextAxis, "create", item, fragment);
                    return;
                }
                previousPress = event.button === 0 ? {time: event.timeStamp, x: event.clientX} : null;
                if (item.name !== model.selected) {
                    model.selected = item.name;
                    model.selectedFragment = 0;
                    model.editingFragment = null;
                    window.clearTimeout(selectTimer);
                    selectTimer = window.setTimeout(flushSelect, 550);
                }
            });
        });

        host.querySelectorAll(".transition-active-track").forEach((track) => {
            let previousPress = null;
            track.addEventListener("pointerdown", (event) => {
                if (event.target.closest(".bin-clip") || event.button !== 0) return;
                const secondPress = previousPress
                    && event.timeStamp - previousPress.time <= 700
                    && Math.abs(event.clientX - previousPress.x) <= 8;
                previousPress = secondPress ? null : {time: event.timeStamp, x: event.clientX};
                if (!secondPress) return;
                const item = selectedItem(model);
                if (!item) return;
                const value = valueFromClientX(axis, model, event.clientX);
                const fragment = addFragment(item, value, value, model);
                model.selectedFragment = fragment;
                render(host, model, original);
                startDrag(event, host, model, original, host.querySelector(".bin-axis"), "create", item, fragment);
            });
        });

        host.querySelectorAll(".bin-handle").forEach((handle) => {
            handle.addEventListener("pointerdown", (event) => {
                const clip = handle.closest(".bin-clip");
                const fragment = Number(clip.dataset.fragment);
                if (clip.dataset.bin !== model.selected || fragment !== model.selectedFragment) {
                    model.selected = clip.dataset.bin;
                    model.selectedFragment = fragment;
                    render(host, model, original);
                    return;
                }
                model.armedEdge = handle.dataset.edge;
                const item = selectedItem(model);
                if (!item || item.kind === "default" || item.automatic) {
                    return;
                }
                startDrag(event, host, model, original, axis, handle.dataset.edge, item, fragment);
            });
        });

        let previousCellPress = null;
        host.querySelectorAll(".bin-cell").forEach((cell) => {
            cell.addEventListener("pointerdown", (event) => {
                if (event.button !== 0) {
                    return;
                }
                const value = Number(cell.dataset.value);
                if (!canCreateAt(model, value)) {
                    return;
                }
                const secondPress = previousCellPress?.cell === cell
                    && event.timeStamp - previousCellPress.time <= 700;
                if (!secondPress) {
                    previousCellPress = {cell, time: event.timeStamp};
                    return;
                }
                previousCellPress = null;
                const item = addBin(model, value);
                render(host, model, original);
                startDrag(event, host, model, original, host.querySelector(".bin-axis"), "create", item, 0);
            });
        });

        let previousAxisPress = null;
        host.querySelector(".bin-axis")?.addEventListener("pointerdown", (event) => {
            if (usesCells(model) || event.button !== 0 || event.target.closest(".bin-row, .bin-clip, .bin-row-name")) {
                return;
            }
            const value = valueFromClientX(axis, model, event.clientX);
            if (!canCreateAt(model, value)) {
                return;
            }
            const secondPress = previousAxisPress
                && event.timeStamp - previousAxisPress.time <= 700
                && Math.abs(event.clientX - previousAxisPress.x) <= 8;
            if (!secondPress) {
                previousAxisPress = {time: event.timeStamp, x: event.clientX};
                return;
            }
            previousAxisPress = null;
            const item = addBin(model, value);
            render(host, model, original);
            startDrag(event, host, model, original, host.querySelector(".bin-axis"), "create", item, 0);
        });

        host.querySelector(".bin-add")?.addEventListener("click", () => {
            const item = addBin(model, firstUncoveredValue(model));
            render(host, model, original);
            host.querySelector(".bin-axis")?.focus();
        });
        host.querySelector(".bin-add-default")?.addEventListener("click", () => {
            if (addDefault(model)) {
                render(host, model, original);
            }
        });
        host.querySelector(".bin-add-transition")?.addEventListener("click", () => {
            const index = addTransitionBin(model);
            model.selectedTransition = index;
            model.transitionActive = null;
            render(host, model, original);
        });

        host.querySelectorAll(".bin-kind").forEach((button) => {
            button.addEventListener("click", () => {
                const transitionIndex = Number(button.dataset.transitionBin);
                if (Number.isInteger(transitionIndex)) {
                    const bin = model.transitionBins?.[transitionIndex];
                    if (!bin) return;
                    bin.coverageKind = button.dataset.kind;
                    render(host, model, original);
                    return;
                }
                const item = selectedItem(model);
                if (!item || item.automatic) {
                    return;
                }
                item.kind = button.dataset.kind;
                render(host, model, original);
            });
        });

        let previousFragmentPress = null;
        const openFragmentEditor = (token) => {
            if (token.closest(".transition-step-selector")) {
                // Transition rows atomically select their own backing item above.
                return;
            }
            const item = selectedItem(model);
            const at = Number(token.dataset.fragment);
            if (!item || item.kind === "default" || item.automatic || !item.fragments[at]) {
                return;
            }
            model.selectedFragment = at;
            model.editingFragment = at;
            model.selectorEditSnapshot = item.fragments.map((pair) => pair.slice());
            model.editingSelector = true;
            model.editingSelectorFocusPending = true;
            render(host, model, original);
            window.requestAnimationFrame(() => {
                host.querySelector(`.bin-fragment-editor[data-fragment="${at}"] input, .bin-fragment-editor[data-fragment="${at}"] select`)?.focus();
                model.editingSelectorFocusPending = false;
            });
        };
        host.querySelectorAll(".bin-fragment-token").forEach((token) => {
            token.addEventListener("pointerdown", (event) => {
                if (event.button !== 0) {
                    return;
                }
                const sameToken = previousFragmentPress?.token === token
                    && event.timeStamp - previousFragmentPress.time <= 900
                    && Math.abs(event.clientX - previousFragmentPress.x) <= 8
                    && Math.abs(event.clientY - previousFragmentPress.y) <= 8;
                if (sameToken) {
                    previousFragmentPress = null;
                    openFragmentEditor(token);
                    return;
                }
                previousFragmentPress = {token, time: event.timeStamp, x: event.clientX, y: event.clientY};
            });
            token.addEventListener("click", (event) => {
                if (event.detail >= 2) {
                    event.preventDefault();
                    openFragmentEditor(token);
                }
            });
            token.addEventListener("dblclick", (event) => {
                event.preventDefault();
                openFragmentEditor(token);
            });
        });
        const selectorEditor = model.transitionActive
            ? host.querySelector(".transition-step-selector.is-selected .bin-selector-syntax")
            : host.querySelector(".bin-editor .bin-selector-syntax");
        if (model.editingSelector && selectorEditor) {
            const closeSelectorEditor = (cancel = false) => {
                const item = selectedItem(model);
                if (cancel && item && model.selectorEditSnapshot) {
                    item.fragments = model.selectorEditSnapshot.map((pair) => pair.slice());
                    refreshItemBounds(item);
                }
                model.editingSelector = false;
                model.editingFragment = null;
                model.selectorEditSnapshot = null;
                model.editingSelectorFocusPending = false;
                if (model.transitionActive) {
                    // The temporary track belongs only to the active transition
                    // selector, so closing it removes that track but preserves
                    // the transition bin's ordinary edit selection.
                    model.transitionActive = null;
                }
                render(host, model, original);
            };
            selectorEditor.addEventListener("keydown", (event) => {
                if (event.key === "Escape") {
                    event.preventDefault();
                    closeSelectorEditor(true);
                } else if (event.key === "Enter") {
                    event.preventDefault();
                    closeSelectorEditor();
                }
            });
        }
        host.querySelectorAll(".bin-enum-lo, .bin-enum-hi").forEach((select) => {
            select.addEventListener("change", (event) => {
                const item = selectedItem(model);
                const at = Number(event.target.dataset.fragment);
                if (!item || item.kind === "default" || item.automatic) {
                    return;
                }
                const loInput = host.querySelector(`.bin-enum-lo[data-fragment="${at}"]`);
                const hiInput = host.querySelector(`.bin-enum-hi[data-fragment="${at}"]`);
                if (!loInput || !hiInput) {
                    return;
                }
                model.selectedFragment = at;
                setRange(item, Number(loInput.value), Number(hiInput.value), model, at);
                render(host, model, original);
            });
        });
        host.querySelectorAll(".bin-enum-value").forEach((select) => {
            select.addEventListener("change", (event) => {
                const item = selectedItem(model);
                const at = Number(event.target.dataset.fragment);
                if (!item || item.kind === "default" || item.automatic) {
                    return;
                }
                model.selectedFragment = at;
                const value = Number(event.target.value);
                setRange(item, value, value, model, at);
                render(host, model, original);
            });
        });
        const applyBounds = (input, live) => {
            const item = selectedItem(model);
            const at = Number(input.dataset.fragment);
            const loInput = host.querySelector(`.bin-lo[data-fragment="${at}"]`);
            const hiInput = host.querySelector(`.bin-hi[data-fragment="${at}"]`);
            if (!item || item.kind === "default" || item.automatic || !loInput || !hiInput) {
                return;
            }
            model.selectedFragment = at;
            setRange(item, Number(loInput.value), Number(hiInput.value), model, at);
            const [lo, hi] = item.fragments[at] || [];
            if (loInput && lo != null) {
                loInput.value = String(lo);
                sizeNumber(loInput);
            }
            if (hiInput && hi != null) {
                hiInput.value = String(hi);
                sizeNumber(hiInput);
            }
            if (live) {
                paintLive(host, model);
            } else {
                render(host, model, original);
            }
        };
        const applyValue = (input, live) => {
            const item = selectedItem(model);
            const at = Number(input.dataset.fragment);
            if (!item || item.kind === "default" || item.automatic || !item.fragments[at]) {
                return;
            }
            model.selectedFragment = at;
            const value = Number(input.value);
            setRange(item, value, value, model, at);
            input.value = String(item.fragments[at][0]);
            sizeNumber(input);
            if (live) {
                paintLive(host, model);
            } else {
                render(host, model, original);
            }
        };
        const wireNumber = (input, edge) => {
            if (!input) {
                return;
            }
            input.addEventListener("focus", () => {
                model.armedEdge = edge;
                model.selectedFragment = Number(input.dataset.fragment);
            });
            input.addEventListener("input", () => applyBounds(input, true));
            input.addEventListener("change", () => applyBounds(input, false));
            input.addEventListener("wheel", (event) => {
                event.preventDefault();
                event.stopPropagation();
                const item = selectedItem(model);
                const at = Number(input.dataset.fragment);
                if (!item || item.kind === "default" || item.automatic || !item.fragments[at]) {
                    return;
                }
                model.armedEdge = edge;
                model.selectedFragment = at;
                const delta = event.deltaY < 0 ? 1 : -1;
                const current = edge === "lo" ? item.fragments[at][0] : item.fragments[at][1];
                if (!setEdge(item, edge, current + delta, model, at)) {
                    return;
                }
                paintLive(host, model);
                persist(host, model);
            }, {passive: false});
        };
        host.querySelectorAll(".bin-lo").forEach((input) => wireNumber(input, "lo"));
        host.querySelectorAll(".bin-hi").forEach((input) => wireNumber(input, "hi"));
        host.querySelectorAll(".bin-value").forEach((input) => {
            input.addEventListener("focus", () => {
                model.selectedFragment = Number(input.dataset.fragment);
            });
            input.addEventListener("input", () => applyValue(input, true));
            input.addEventListener("change", () => applyValue(input, false));
            input.addEventListener("wheel", (event) => {
                event.preventDefault();
                event.stopPropagation();
                const item = selectedItem(model);
                const at = Number(input.dataset.fragment);
                if (!item || item.kind === "default" || item.automatic || !item.fragments[at]) {
                    return;
                }
                const value = item.fragments[at][0] + (event.deltaY < 0 ? 1 : -1);
                setRange(item, value, value, model, at);
                paintLive(host, model);
                persist(host, model);
            }, {passive: false});
        });

        canvas.addEventListener("keydown", (event) => {
            if (event.target.matches("input, select, button")) {
                return;
            }
            if (event.key === "Delete") {
                const item = selectedItem(model);
                const at = model.selectedFragment;
                if (!item || item.kind === "default" || item.automatic || !item.fragments[at]) {
                    return;
                }
                event.preventDefault();
                removeFragment(item, at);
                model.selectedFragment = Math.max(0, Math.min(at, item.fragments.length - 1));
                model.editingFragment = null;
                render(host, model, original);
                host.querySelector(".bin-axis")?.focus();
                return;
            }
            if (!["ArrowLeft", "ArrowRight"].includes(event.key)) {
                return;
            }
            event.preventDefault();
            const delta = event.key === "ArrowRight" ? 1 : -1;
            if (nudge(model, model.armedEdge || "hi", delta)) {
                render(host, model, original);
                axis.focus();
            }
        });
    }

    function startDeleteDrag(event, host, model, original, clip, item, fragment) {
        event.preventDefault();
        event.stopPropagation();
        const pointerId = event.pointerId;
        const valueCard = host.querySelector(".bin-axis");
        const root = document.documentElement;
        const rect = clip.getBoundingClientRect();
        const offsetX = event.clientX - rect.left;
        const offsetY = event.clientY - rect.top;
        const ghost = document.createElement("div");
        ghost.className = "bin-delete-ghost";
        ghost.style.width = `${rect.width}px`;
        ghost.style.height = `${rect.height}px`;
        ghost.style.background = getComputedStyle(clip).backgroundColor;
        ghost.innerHTML = "<span>松开删除</span>";
        document.body.append(ghost);
        let dragging = true;
        let leftPage = false;

        const insidePage = (next) => next.clientX >= 0 && next.clientY >= 0
            && next.clientX <= window.innerWidth && next.clientY <= window.innerHeight;
        const insidePanel = (next) => {
            if (!valueCard) {
                return false;
            }
            const bounds = valueCard.getBoundingClientRect();
            return next.clientX >= bounds.left && next.clientX <= bounds.right
                && next.clientY >= bounds.top && next.clientY <= bounds.bottom;
        };
        const cleanup = () => {
            root.classList.remove("bin-dragging");
            root.style.removeProperty("--bin-drag-cursor");
            root.style.removeProperty("cursor");
            ghost.remove();
            window.removeEventListener("pointermove", move, true);
            window.removeEventListener("pointerup", up, true);
            window.removeEventListener("pointercancel", up, true);
            window.removeEventListener("blur", abandon, true);
        };
        const abandon = () => {
            leftPage = true;
            ghost.classList.remove("is-visible");
        };
        const move = (next) => {
            if (!dragging || next.pointerId !== pointerId || leftPage) {
                return;
            }
            if (!insidePage(next)) {
                abandon();
                return;
            }
            if (insidePanel(next)) {
                ghost.classList.remove("is-visible");
                root.style.setProperty("--bin-drag-cursor", "not-allowed");
                return;
            }
            ghost.style.left = `${next.clientX - offsetX}px`;
            ghost.style.top = `${next.clientY - offsetY}px`;
            ghost.classList.add("is-visible");
            root.style.setProperty("--bin-drag-cursor", "grabbing");
        };
        const up = (next) => {
            if (!dragging || (next && next.pointerId !== pointerId)) {
                return;
            }
            dragging = false;
            const remove = Boolean(next && !leftPage && insidePage(next) && !insidePanel(next));
            cleanup();
            if (remove) {
                removeFragment(item, fragment);
                model.selectedFragment = Math.max(0, Math.min(fragment, item.fragments.length - 1));
                model.editingFragment = null;
            }
            render(host, model, original);
            host.querySelector(".bin-axis")?.focus();
        };
        root.classList.add("bin-dragging");
        root.style.setProperty("--bin-drag-cursor", "not-allowed");
        try {
            clip.setPointerCapture(pointerId);
        } catch (_error) {
            /* The window listeners still receive the drag. */
        }
        window.addEventListener("pointermove", move, true);
        window.addEventListener("pointerup", up, true);
        window.addEventListener("pointercancel", up, true);
        window.addEventListener("blur", abandon, true);
    }

    function startDrag(event, host, model, original, axis, mode, item, fragment) {
        event.preventDefault();
        event.stopPropagation();
        const pointerId = event.pointerId;
        const cursor = mode === "move" ? "grabbing" : "ew-resize";
        const root = document.documentElement;
        const at = Number.isInteger(fragment) ? fragment : fragmentIndex(model, item);
        if (at < 0 || !item.fragments[at] || !axis) {
            return;
        }
        model.selectedFragment = at;
        let dragging = true;
        root.classList.add("bin-dragging");
        root.style.setProperty("--bin-drag-cursor", cursor);
        root.style.cursor = cursor;
        try {
            axis.setPointerCapture(pointerId);
        } catch (_error) {
            /* Cursor lock still holds if capture is unavailable. */
        }
        const origin = valueFromClientX(axis, model, event.clientX);
        const startLo = item.fragments[at][0];
        const startHi = item.fragments[at][1];
        const move = (next) => {
            if (!dragging || next.pointerId !== pointerId) {
                return;
            }
            const value = valueFromClientX(axis, model, next.clientX);
            if (mode === "create") {
                setRange(item, origin, value, model, at);
            } else if (mode === "lo") {
                setRange(item, value, startHi, model, at);
            } else if (mode === "hi") {
                setRange(item, startLo, value, model, at);
            } else {
                const delta = value - origin;
                const width = startHi - startLo;
                let lo = startLo + delta;
                let hi = startHi + delta;
                if (lo < model.min) {
                    lo = model.min;
                    hi = lo + width;
                }
                if (hi > model.max) {
                    hi = model.max;
                    lo = hi - width;
                }
                setRange(item, lo, hi, model, at);
            }
            paintLive(host, model);
        };
        const up = (next) => {
            if (!dragging || (next && next.pointerId !== pointerId)) {
                return;
            }
            dragging = false;
            root.classList.remove("bin-dragging");
            root.style.removeProperty("--bin-drag-cursor");
            root.style.removeProperty("cursor");
            window.removeEventListener("pointermove", move, true);
            window.removeEventListener("pointerup", up, true);
            window.removeEventListener("pointercancel", up, true);
            render(host, model, original);
            axis.focus();
        };
        window.addEventListener("pointermove", move, true);
        window.addEventListener("pointerup", up, true);
        window.addEventListener("pointercancel", up, true);
    }

    function wireTransitionChrome(host, model) {
        if (model.embedded) return;
        const panel = host.closest(".bin-canvas-panel");
        if (!panel) return;
        panel.querySelector(".bin-domain-overview")?.classList.add("hidden");
        const undoButton = panel.querySelector(".bin-undo");
        const redoButton = panel.querySelector(".bin-redo");
        const history = transitionHistory(host);
        if (undoButton) undoButton.disabled = !history?.undo.length;
        if (redoButton) redoButton.disabled = !history?.redo.length;
        const help = panel.querySelector(".bin-help");
        const helpText = help?.querySelector(".bin-help-popover");
        if (helpText) helpText.textContent = "编辑 transition 的有序状态序列。状态项可直接编辑；重复项可修改次数。所有修改仅保留在当前页面。";
        if (panel.dataset.transitionChromeWired === "true") return;
        panel.dataset.transitionChromeWired = "true";
        panel.querySelector(".bin-reset")?.addEventListener("click", () => {
            transitionDrafts.delete(host.dataset.canvasKey);
            histories.delete(host.dataset.canvasKey);
            Object.assign(model, transitionModelFromBins(host._transitionOriginal));
            renderTransitionEditor(host, model);
        });
        undoButton?.addEventListener("click", () => transitionUndo(host, host._transitionModel));
        redoButton?.addEventListener("click", () => transitionRedo(host, host._transitionModel));
        help?.querySelector("summary")?.addEventListener("mouseleave", () => { help.open = false; });
    }

    function renderTransitionEditor(host, model) {
        host._transitionModel = model;
        host._binModel = null;
        recordTransitionHistory(host, model);
        transitionDrafts.set(host.dataset.canvasKey, cloneTransitionModel(model));
        const cards = model.bins.map((bin, binIndex) => `<section class="transition-bin" data-transition-bin="${binIndex}">
            <div class="transition-bin-head"><input class="transition-bin-name" value="${esc(bin.name)}" aria-label="transition bin 名称"><span class="transition-bin-label">transition</span><span class="transition-bin-tools"><button type="button" class="transition-bin-move" data-direction="-1" title="上移" ${binIndex ? "" : "disabled"}>↑</button><button type="button" class="transition-bin-move" data-direction="1" title="下移" ${binIndex + 1 < model.bins.length ? "" : "disabled"}>↓</button><button type="button" class="transition-bin-delete" title="删除 transition bin">×</button></span></div>
            <div class="transition-steps">${bin.steps.map((step, stepIndex) => `<div class="transition-step" data-step="${stepIndex}">${step.editing ? `<input class="transition-step-text" value="${esc(step.text)}" aria-label="第 ${stepIndex + 1} 个状态 selector">` : `<button type="button" class="transition-step-chip" title="双击编辑 selector">${esc(step.text)}</button>`}${step.repeat ? `<span class="transition-repeat">[*<input class="transition-repeat-min" type="number" min="0" value="${esc(step.minimum)}" aria-label="最小重复次数">:<input class="transition-repeat-max" type="number" min="0" value="${esc(step.maximum)}" aria-label="最大重复次数">]</span>` : ""}<span class="transition-step-tools"><button type="button" class="transition-repeat-toggle" title="${step.repeat ? "移除重复" : "设置重复"}">${step.repeat ? "[*]" : "＋*"}</button><button type="button" class="transition-step-delete" title="删除状态" ${bin.steps.length <= 2 ? "disabled" : ""}>×</button></span>${stepIndex + 1 < bin.steps.length ? '<span class="transition-arrow">→</span>' : ""}</div>`).join("")}</div>
            <button type="button" class="transition-step-add">＋ 新增状态</button>
        </section>`).join("");
        host.innerHTML = `<div class="transition-domain transition-editor"><p class="muted">transition 以有序状态序列编辑；这不是数值滑轨。</p>${cards}<button type="button" class="transition-bin-add">＋ 新建 transition bin</button></div>`;
        syncTransitionBinsPanel(host, model);
        wireTransitionChrome(host, model);
        host.querySelectorAll(".transition-bin").forEach((card) => {
            const bin = model.bins[Number(card.dataset.transitionBin)];
            card.querySelector(".transition-bin-name")?.addEventListener("change", (event) => { bin.name = event.target.value.trim() || bin.name; renderTransitionEditor(host, model); });
            card.querySelectorAll(".transition-step").forEach((node) => {
                const step = bin.steps[Number(node.dataset.step)];
                node.querySelector(".transition-step-chip")?.addEventListener("dblclick", () => { step.editing = true; renderTransitionEditor(host, model); host.querySelector(`[data-transition-bin="${card.dataset.transitionBin}"] .transition-step[data-step="${node.dataset.step}"] .transition-step-text`)?.focus(); });
                node.querySelector(".transition-step-text")?.addEventListener("change", (event) => { step.text = event.target.value.trim() || step.text; step.editing = false; renderTransitionEditor(host, model); });
                node.querySelector(".transition-step-text")?.addEventListener("keydown", (event) => { if (event.key === "Escape") { step.editing = false; renderTransitionEditor(host, model); } });
                node.querySelector(".transition-repeat-toggle")?.addEventListener("click", () => { step.repeat = !step.repeat; step.minimum ??= 1; step.maximum ??= 1; renderTransitionEditor(host, model); });
                node.querySelector(".transition-repeat-min")?.addEventListener("change", (event) => { step.minimum = Math.max(0, Number(event.target.value) || 0); if (step.maximum < step.minimum) step.maximum = step.minimum; renderTransitionEditor(host, model); });
                node.querySelector(".transition-repeat-max")?.addEventListener("change", (event) => { step.maximum = Math.max(step.minimum, Number(event.target.value) || 0); renderTransitionEditor(host, model); });
                node.querySelector(".transition-step-delete")?.addEventListener("click", () => { bin.steps.splice(Number(node.dataset.step), 1); renderTransitionEditor(host, model); });
            });
            card.querySelector(".transition-step-add")?.addEventListener("click", () => { bin.steps.push({repeat: false, text: "{0}", editing: false}); renderTransitionEditor(host, model); });
            card.querySelectorAll(".transition-bin-move").forEach((button) => button.addEventListener("click", () => { const at = Number(card.dataset.transitionBin); const next = at + Number(button.dataset.direction); [model.bins[at], model.bins[next]] = [model.bins[next], model.bins[at]]; renderTransitionEditor(host, model); }));
            card.querySelector(".transition-bin-delete")?.addEventListener("click", () => { model.bins.splice(Number(card.dataset.transitionBin), 1); renderTransitionEditor(host, model); });
        });
        host.querySelector(".transition-bin-add")?.addEventListener("click", () => { model.bins.push({name: uniqueTransitionName(model), steps: [{repeat: false, text: "{0}", editing: false}, {repeat: false, text: "{1}", editing: false}]}); renderTransitionEditor(host, model); });
    }

    function mountBinCanvas(host, bins, key, point) {
        if (!host) {
            return false;
        }
        host.dataset.canvasKey = key || "";
        activateHistoryShortcuts(host);
        const transitions = (bins || []).filter((bin) => bin.kind === "transition");
        if (transitions.length) {
            const model = modelFromBins(bins, axisFromPoint(point));
            if (!model) {
                host.innerHTML = "<p class='muted'>这个 coverpoint 没有可编辑的整数值域。</p>";
                return false;
            }
            const saved = transitionDrafts.get(`${host.dataset.canvasKey}::transition`);
            const transitionModel = saved ? cloneTransitionModel(saved) : transitionModelFromBins(bins);
            model.transitionBins = transitionModel.bins;
            model.extraBins = transitionModel.bins.map((bin) => ({name: bin.name, selector: transitionSyntax(bin)}));
            restore(host, model);
            render(host, model, bins);
            return true;
        }
        host._transitionModel = null;
        host._transitionOriginal = null;
        host.closest(".bin-canvas-panel")?.querySelector(".bin-domain-overview")?.classList.remove("hidden");
        const model = modelFromBins(bins, axisFromPoint(point));
        if (!model) {
            host.innerHTML = "<p class='muted'>这个 coverpoint 不是小整数闭区间，暂不提供值域编辑。</p>";
            return false;
        }
        restore(host, model);
        render(host, model, bins);
        return true;
    }

    function wireBinCanvas(point, bins) {
        const host = document.querySelector(".bin-canvas-host");
        if (!host) {
            return;
        }
        if (!point) {
            host.innerHTML = "";
            return;
        }
        const layoutKey = bins
            ? `${point.semantic_id || point.name}::layout::${JSON.stringify(bins)}`
            : (point.semantic_id || point.name);
        mountBinCanvas(host, bins || point.bins || [], layoutKey, point);
        installPointDslCard(host, point);
    }

    function installCrossDslCard() {
        const detail = document.querySelector("#detail");
        const hero = detail?.querySelector(".hero");
        if (!detail || !hero || detail.querySelector(".dsl-card") || !hero.querySelector(".eyebrow")?.textContent.startsWith("Cross")) {
            return;
        }
        const name = hero.querySelector("h2")?.textContent?.trim() || "cross";
        const members = [...hero.querySelectorAll(".chips .chip")].map((chip) => chip.textContent.trim()).filter(Boolean);
        const binPanel = [...detail.querySelectorAll(".panel")].find((panel) => panel.querySelector("h3")?.textContent === "Cross bins");
        const lines = [`class ${name}(Cross, members=(${members.join(", ")})):`];
        for (const row of binPanel?.querySelectorAll("tbody tr") || []) {
            const cells = row.querySelectorAll("td");
            if (cells.length < 3) continue;
            const family = cells[1].textContent.trim() === "ignore" ? "ignore_bins"
                : cells[1].textContent.trim() === "illegal" ? "illegal_bins" : "bins";
            lines.push(`    ${cells[0].textContent.trim()} = ${family}[${cells[2].textContent.trim()}]`);
        }
        if (lines.length === 1) lines.push("    pass");
        const card = document.createElement("details");
        card.className = "panel dsl-card";
        card.open = true;
        card.innerHTML = "<summary class='panel-head'><h3>DSL</h3><span class='muted'>当前声明</span></summary><div class='panel-body'><pre class='definition'><code></code></pre></div>";
        const source = detail.querySelector(".dsl-source")?.textContent;
        card.querySelector("code").innerHTML = highlightDsl(source || lines.join("\n"));
        hero.insertAdjacentElement("afterend", card);
    }

    new MutationObserver(installCrossDslCard).observe(document.documentElement, {childList: true, subtree: true});

    global.mountBinCanvas = mountBinCanvas;
    global.wireBinCanvas = wireBinCanvas;
    global.coverpointBinModel = modelFromBins;
})(window);
