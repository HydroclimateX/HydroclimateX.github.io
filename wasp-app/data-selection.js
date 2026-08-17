(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.WaspDataSelection = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function parseCsvHeader(csvText) {
    if (typeof csvText !== 'string') throw new TypeError('CSV content must be text.');
    const text = csvText.replace(/^\uFEFF/, '');
    const columns = [];
    let field = '';
    let quoted = false;
    let index = 0;

    while (index < text.length) {
      const character = text[index];
      if (quoted) {
        if (character === '"') {
          if (text[index + 1] === '"') {
            field += '"';
            index += 2;
            continue;
          }
          quoted = false;
        } else {
          field += character;
        }
      } else if (character === '"' && field.length === 0) {
        quoted = true;
      } else if (character === ',') {
        columns.push(field);
        field = '';
      } else if (character === '\n' || character === '\r') {
        columns.push(field);
        break;
      } else {
        field += character;
      }
      index += 1;
    }

    if (quoted) throw new Error('CSV header contains an unterminated quoted value.');
    if (index >= text.length) columns.push(field);
    if (columns.length < 2) throw new Error('CSV must contain a predictand and at least one predictor.');
    if (columns.some(column => !column.trim())) throw new Error('CSV column headers must not be blank.');
    if (new Set(columns).size !== columns.length) throw new Error('CSV column headers must be unique.');
    return columns;
  }

  function createSelection(columns) {
    if (!Array.isArray(columns) || columns.length < 2) {
      throw new Error('At least two columns are required.');
    }
    return {
      columns: columns.slice(),
      targetColumn: columns[0],
      predictorColumns: columns.slice(1),
    };
  }

  function changeTarget(selection, targetColumn) {
    if (!selection.columns.includes(targetColumn)) throw new Error('Unknown predictand column.');
    const selected = new Set(selection.predictorColumns);
    selected.add(selection.targetColumn);
    selected.delete(targetColumn);
    return {
      columns: selection.columns.slice(),
      targetColumn,
      predictorColumns: selection.columns.filter(
        column => column !== targetColumn && selected.has(column)
      ),
    };
  }

  function clearPredictors(selection) {
    return {...selection, columns: selection.columns.slice(), predictorColumns: []};
  }

  function selectAllPredictors(selection) {
    return {
      ...selection,
      columns: selection.columns.slice(),
      predictorColumns: selection.columns.filter(column => column !== selection.targetColumn),
    };
  }

  function isSelectionValid(selection) {
    if (!selection || !selection.targetColumn || !Array.isArray(selection.predictorColumns)) return false;
    if (!Array.isArray(selection.columns) || !selection.columns.includes(selection.targetColumn)) return false;
    if (selection.predictorColumns.length < 1) return false;
    const predictors = new Set(selection.predictorColumns);
    return predictors.size === selection.predictorColumns.length
      && !predictors.has(selection.targetColumn)
      && selection.predictorColumns.every(column => selection.columns.includes(column));
  }

  function appendPredictionFields(formData, selection, model) {
    if (!isSelectionValid(selection)) throw new Error('Select one predictand and at least one predictor.');
    formData.append('target_column', selection.targetColumn);
    selection.predictorColumns.forEach(column => formData.append('predictor_columns', column));
    formData.append('model', model);
    return formData;
  }

  function attributionPresentation(attributions, modelLabel) {
    const value = attributions || {kind: 'none', items: []};
    if (value.kind === 'coefficient') {
      return {kind: value.kind, title: `Scaled coefficients — ${modelLabel}`, message: '', items: value.items || []};
    }
    if (value.kind === 'importance') {
      return {kind: value.kind, title: `Feature importance — ${modelLabel}`, message: '', items: value.items || []};
    }
    return {
      kind: 'none',
      title: `Model interpretation — ${modelLabel}`,
      message: `${modelLabel} has no intrinsic feature attribution.`,
      items: [],
    };
  }

  function createOperationGate() {
    let generation = 0;
    return {
      begin() {
        generation += 1;
        return generation;
      },
      invalidate() {
        generation += 1;
      },
      isCurrent(token) {
        return token === generation;
      },
    };
  }

  return {
    parseCsvHeader,
    createSelection,
    changeTarget,
    clearPredictors,
    selectAllPredictors,
    isSelectionValid,
    appendPredictionFields,
    attributionPresentation,
    createOperationGate,
  };
}));
