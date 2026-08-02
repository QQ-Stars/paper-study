(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MarkdownRendering = api;
}(typeof self !== 'undefined' ? self : (typeof window !== 'undefined' ? window : undefined), function () {
  function text(value) {
    if (value == null) return '';
    try {
      return String(value);
    } catch (error) {
      return '';
    }
  }

  function escapeHtml(value) {
    return text(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function hasPathologicalEmphasisParagraph(input) {
    const minimumLength = 4096;
    const minimumMarkers = 2048;
    const minimumDensity = 0.35;
    let index = 0;
    let runLength = 0;
    let runMarkers = 0;

    function isPathologicalRun() {
      return runLength >= minimumLength
        && runMarkers >= minimumMarkers
        && runMarkers / runLength >= minimumDensity;
    }

    // Scan code-fence-looking text too: a partial fence parser would make this
    // preflight bypassable whenever its syntax differs from Marked's parser.
    while (index < input.length) {
      let lineLength = 0;
      let lineMarkers = 0;
      let blankLine = true;

      while (index < input.length && input[index] !== '\r' && input[index] !== '\n') {
        const character = input[index++];
        lineLength++;
        if (character === '*' || character === '_') lineMarkers++;
        if (!/\s/.test(character)) blankLine = false;
      }

      if (blankLine) {
        if (isPathologicalRun()) return true;
        runLength = 0;
        runMarkers = 0;
      } else {
        runLength += lineLength;
        runMarkers += lineMarkers;
      }

      if (input[index] === '\r' && input[index + 1] === '\n') index += 2;
      else if (index < input.length) index++;
    }

    return isPathologicalRun();
  }

  function safeHref(value) {
    const href = text(value).trim();
    if (!href) return null;
    try {
      const url = new URL(href);
      if (url.protocol === 'mailto:' && /^mailto:/i.test(href)) return href;
      return /^(https?:)$/i.test(url.protocol) && /^https?:\/\//i.test(href) ? href : null;
    } catch (error) {
      return null;
    }
  }

  function escapeParityBefore(input, position) {
    let escaped = false;
    for (let index = position - 1; index >= 0 && input[index] === '\\'; index--) escaped = !escaped;
    return escaped;
  }

  function isEscaped(input, position) {
    return escapeParityBefore(input, position);
  }

  function findDelimiter(input, delimiter, start) {
    let position = input.indexOf(delimiter, start);
    while (position >= 0) {
      if (!isEscaped(input, position)) return position;
      position = input.indexOf(delimiter, position + delimiter.length);
    }
    return -1;
  }

  function markedInlineHtmlRaw(source, lexer) {
    const tagRule = lexer && lexer.tokenizer && lexer.tokenizer.rules && lexer.tokenizer.rules.inline && lexer.tokenizer.rules.inline.tag;
    if (!tagRule) return '';
    tagRule.lastIndex = 0;
    const match = tagRule.exec(source);
    tagRule.lastIndex = 0;
    return match && match.index === 0 ? match[0] : '';
  }

  function isUnsafeHtmlLikeStart(source, position, end, lexer) {
    const candidate = source.slice(position, end);
    if (candidate.startsWith('<!--') || candidate.startsWith('<?') || /^<!\[CDATA\[/i.test(candidate)) return true;
    if (candidate[1] === '!' && /[A-Za-z]/.test(candidate[2] || '')) return true;
    if (markedInlineHtmlRaw(candidate, lexer)) return true;

    const malformed = htmlLikeToken(candidate);
    if (!malformed) return false;
    return malformed.raw.includes('>') || /^<\/?[A-Za-z][A-Za-z0-9:-]*(?:[\s/=])/.test(candidate);
  }

  function isAutolinkStart(source, position, end, lexer) {
    const candidate = source.slice(position, end);
    const tokenizer = lexer && lexer.tokenizer;
    if (tokenizer && typeof tokenizer.autolink === 'function') return Boolean(tokenizer.autolink(candidate));
    return /^<(?:https?:\/\/[^\s<>]+|mailto:[^\s<>]+|[^\s<>@]+@[^\s<>@]+)>/i.test(candidate);
  }

  function isBareGfmUrlStart(source, position, end, lexer) {
    const candidate = source.slice(position, end);
    if (!/^(?:https?:\/\/|www\.)/i.test(candidate)) return false;
    const tokenizer = lexer && lexer.tokenizer;
    if (tokenizer && typeof tokenizer.url === 'function') return Boolean(tokenizer.url(candidate));
    return /^(?:https?:\/\/|www\.)[^\s<]+/i.test(candidate);
  }

  function rawMarkdownLinkLabel(source) {
    if (source[0] !== '[') return '';

    let depth = 0;
    let escaped = false;
    for (let position = 0; position < source.length; position++) {
      const character = source[position];
      if (character === '\\') {
        escaped = !escaped;
        continue;
      }
      if (!escaped && character === '[') {
        depth++;
      } else if (!escaped && character === ']') {
        depth--;
        if (depth === 0) return source.slice(1, position);
      }
      escaped = false;
    }
    return '';
  }

  function crossesUnsafeMarkdownLinkContext(source, start, end) {
    const labels = [];
    let destination = null;
    let escaped = escapeParityBefore(source, start);
    let previousEscaped = source[start - 1] === '!' && escapeParityBefore(source, start - 1);

    for (let position = start; position < end; position++) {
      const character = source[position];
      const characterEscaped = escaped;

      if (!characterEscaped) {
        if (destination) {
          if (character === '(') destination.depth++;
          else if (character === ')') {
            destination.depth--;
            if (destination.depth === 0) {
              return true;
            }
          }
        } else if (character === '[') {
          labels.push({ start: position, image: source[position - 1] === '!' && !previousEscaped });
        } else if (character === ']' && labels.length > 0) {
          const label = labels.pop();
          if (labels.length === 0 && source[position + 1] === '(') {
            destination = {
              depth: 1,
              start: position + 2,
              image: label.image,
              labelStart: label.start,
              labelEnd: position,
            };
            position++;
          }
        }
      }

      previousEscaped = characterEscaped;
      escaped = character === '\\' ? !escaped : false;
    }

    return Boolean(destination);
  }

  function normalizeReferenceLabel(label) {
    return text(label).replace(/\s+/g, ' ').trim().toLowerCase();
  }

  function hasResolvableMarkdownReference(source, links) {
    if (!links) return false;
    const input = text(source);
    const hasReference = label => Object.prototype.hasOwnProperty.call(links, normalizeReferenceLabel(label));
    let labelStart = -1;
    let pendingReference = null;
    let escaped = false;

    for (let position = 0; position < input.length; position++) {
      const character = input[position];
      if (!escaped) {
        if (pendingReference) {
          if (character === '\r' || character === '\n') {
            if (hasReference(pendingReference.label)) return true;
            pendingReference = null;
            labelStart = -1;
          } else if (character === ']') {
            const reference = input.slice(pendingReference.start, position);
            if (hasReference(reference || pendingReference.label)) return true;
            pendingReference = null;
            labelStart = -1;
          }
        } else if (character === '[') {
          labelStart = position + 1;
        } else if (character === '\r' || character === '\n') {
          labelStart = -1;
        } else if (character === ']' && labelStart >= 0) {
          const label = input.slice(labelStart, position);
          labelStart = -1;
          if (input[position + 1] === '[') {
            pendingReference = { label, start: position + 2 };
            position++;
          } else if (input[position + 1] !== '(' && hasReference(label)) {
            return true;
          }
        }
      }
      escaped = character === '\\' ? !escaped : false;
    }

    return Boolean(pendingReference && hasReference(pendingReference.label));
  }

  function crossesUnsafeMarkdownReferenceContext(source, start, end, lexer) {
    const links = lexer && lexer.tokens && lexer.tokens.links;
    return hasResolvableMarkdownReference(source.slice(start, end), links);
  }

  function crossesUnsafeInlineContext(source, start, end, lexer) {
    let escaped = escapeParityBefore(source, start);
    for (let position = start; position < end; position++) {
      const character = source[position];
      if (!escaped) {
        if (character === '`') return true;
        if (
          character === '<' &&
          (isUnsafeHtmlLikeStart(source, position, end, lexer) || isAutolinkStart(source, position, end, lexer))
        ) return true;
        if (isBareGfmUrlStart(source, position, end, lexer)) return true;
      }
      escaped = character === '\\' ? !escaped : false;
    }
    return crossesUnsafeMarkdownLinkContext(source, start, end) || crossesUnsafeMarkdownReferenceContext(source, start, end, lexer);
  }

  function isSelfClosingTag(input, nameEnd, end) {
    if (input[end - 1] !== '/') return false;

    let state = 'beforeAttribute';
    let quote = '';
    for (let cursor = nameEnd; cursor < end - 1; cursor++) {
      const character = input[cursor];
      if (quote) {
        if (character === quote) {
          quote = '';
          state = 'afterValue';
        }
        continue;
      }
      if (state === 'beforeAttribute' || state === 'afterValue') {
        if (!/\s/.test(character)) state = 'attributeName';
        continue;
      }
      if (state === 'attributeName') {
        if (character === '=') state = 'beforeValue';
        else if (/\s/.test(character)) state = 'afterAttributeName';
        continue;
      }
      if (state === 'afterAttributeName') {
        if (character === '=') state = 'beforeValue';
        else if (!/\s/.test(character)) state = 'attributeName';
        continue;
      }
      if (state === 'beforeValue') {
        if (/\s/.test(character)) continue;
        if (character === '"' || character === "'") quote = character;
        else state = 'unquotedValue';
        continue;
      }
      if (state === 'unquotedValue' && /\s/.test(character)) state = 'afterValue';
    }

    return !quote && state !== 'beforeValue' && state !== 'unquotedValue';
  }

  function literalUnclosedBackslashMath(source, opening) {
    if (opening[0] !== '\\') return null;
    let end = 0;
    while (source.startsWith(opening, end)) end += opening.length;
    return {
      type: 'text',
      raw: source.slice(0, end),
      text: opening[1].repeat(end / opening.length),
      escaped: true,
    };
  }

  function delimitedMathToken(source, opening, closing, display, lexer, deferRejectedDisplayCloser) {
    const end = findDelimiter(source, closing, opening.length);
    if (end < 0) return literalUnclosedBackslashMath(source, opening);
    if (end === opening.length) return null;
    if (crossesUnsafeInlineContext(source, opening.length, end, lexer)) {
      if (display && opening === '$$' && typeof deferRejectedDisplayCloser === 'function') {
        deferRejectedDisplayCloser(source.slice(end));
      }
      return null;
    }
    return {
      type: 'math',
      raw: source.slice(0, end + closing.length),
      text: source.slice(opening.length, end),
      display,
    };
  }

  function inlineDollarMathToken(source, lexer) {
    if (source[0] !== '$' || source.startsWith('$$') || !source[1] || /\s/.test(source[1])) return null;

    let end = source.indexOf('$', 1);
    while (end >= 0) {
      if (!isEscaped(source, end)) {
        const formula = source.slice(1, end);
        if (!formula || /\s$/.test(formula) || /[\r\n]/.test(formula)) return null;
        if (/^\d+(?:[.,]\d+)*-$/.test(formula) && /\d/.test(source[end + 1] || '')) return null;
        if (crossesUnsafeInlineContext(source, 1, end, lexer)) return null;
        return { type: 'math', raw: source.slice(0, end + 1), text: formula, display: false };
      }
      end = source.indexOf('$', end + 1);
    }
    return null;
  }

  function mathToken(source, lexer, deferRejectedDisplayCloser) {
    if (source.startsWith('$$')) return delimitedMathToken(source, '$$', '$$', true, lexer, deferRejectedDisplayCloser);
    if (source.startsWith('\\[')) return delimitedMathToken(source, '\\[', '\\]', true, lexer);
    if (source.startsWith('\\(')) return delimitedMathToken(source, '\\(', '\\)', false, lexer);
    return inlineDollarMathToken(source, lexer);
  }

  function hasSquareBracketDisplayMathMarker(source) {
    let slashRun = 0;

    for (let position = 0; position < source.length; position++) {
      const character = source[position];
      if (character === '\\') {
        slashRun++;
        continue;
      }
      if (slashRun % 2 === 1 && character === '[') return true;
      slashRun = 0;
    }
    return false;
  }

  function markdownLinkLabelMathToken(source, lexer) {
    if (source[0] !== '[') return null;

    const inlineTokens = lexer.inlineTokens;
    const inlineTokensDescriptor = Object.getOwnPropertyDescriptor(lexer, 'inlineTokens');
    let token;
    try {
      lexer.inlineTokens = () => [];
      token = lexer.tokenizer.link(source) || lexer.tokenizer.reflink(source, lexer.tokens.links);
    } finally {
      restoreOwnProperty(lexer, 'inlineTokens', inlineTokensDescriptor);
    }
    if (!token || token.type !== 'link') return null;

    const rawLabel = rawMarkdownLinkLabel(token.raw);
    if (!rawLabel || !hasSquareBracketDisplayMathMarker(rawLabel)) return null;
    token.tokens = inlineTokens.call(lexer, rawLabel);
    return token;
  }

  function findMathStart(source) {
    let slashRun = 0;
    let squareClosing = null;
    let parenClosing = null;

    for (let index = 0; index < source.length; index++) {
      const character = source[index];
      const escaped = slashRun % 2 === 1;
      if (character === '$' && !escaped && !isInsideBareUrl(source, index)) return index;
      if (character === '\\' && !escaped && (source[index + 1] === '(' || source[index + 1] === '[')) {
        const closing = source[index + 1] === '[' ? squareClosing : parenClosing;
        const delimiter = source[index + 1] === '[' ? '\\]' : '\\)';
        let nextClosing = closing === null ? findDelimiter(source, delimiter, index + 2) : closing;
        while (nextClosing >= 0 && nextClosing < index + 2) {
          nextClosing = findDelimiter(source, delimiter, nextClosing + delimiter.length);
        }
        if (source[index + 1] === '[') squareClosing = nextClosing;
        else parenClosing = nextClosing;
        if (nextClosing >= 0 && !isInsideBareUrl(source, index)) return index;
      }
      slashRun = character === '\\' ? slashRun + 1 : 0;
    }
    return -1;
  }

  function isInsideBareUrl(source, index) {
    const prefix = source.slice(0, index);
    return /(?:^|[\s(])(?:[A-Za-z][A-Za-z0-9+.-]*:\/\/|w{2,3}\.)[^\s<]*$/i.test(prefix);
  }

  function htmlToken(raw) {
    return { type: 'html', raw, text: raw };
  }

  function htmlLikeToken(source) {
    if (!/^<\/?[A-Za-z]/.test(source)) return null;
    if (/^<(?:https?:\/\/|mailto:)/i.test(source) || /^<[^<>\s@]+@[^<>\s@]+>/.test(source)) return null;

    let quote = '';
    for (let index = 1; index < source.length; index++) {
      const character = source[index];
      if (quote) {
        if (character === quote) quote = '';
        continue;
      }
      if (character === '"' || character === "'") {
        quote = character;
        continue;
      }
      if (character === '>') return htmlToken(source.slice(0, index + 1));
    }

    return htmlToken(source);
  }

  function malformedHtmlLikeToken(source, lexer) {
    if (markedInlineHtmlRaw(source, lexer)) return null;
    if (source.startsWith('<!--')) {
      const end = source.indexOf('-->', 4);
      return htmlToken(end < 0 ? source : source.slice(0, end + 3));
    }
    return htmlLikeToken(source);
  }

  const rawHtmlVoidTags = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
  const rawHtmlTextTags = new Set(['script', 'style', 'xmp', 'iframe', 'noembed', 'noframes', 'textarea', 'title']);

  function closeRawHtmlContext(state, name) {
    const openTag = state.openTags.lastIndexOf(name);
    if (openTag < 0) return;
    const closedTags = state.openTags.slice(openTag);
    state.pendingMath = state.pendingMath.filter(record => {
      if (record.openTags.some(tag => closedTags.includes(tag))) {
        literalMath(record.token);
        return false;
      }
      return true;
    });
    state.openTags.length = openTag;
  }

  function rawTextClosingTagEnd(input, start, name) {
    if (input[start] !== '<' || input[start + 1] !== '/') return -1;
    const nameEnd = start + 2 + name.length;
    if (input.slice(start + 2, nameEnd).toLowerCase() !== name) return -1;
    if (input[nameEnd] && !/[\s/>]/.test(input[nameEnd])) return -1;

    let cursor = nameEnd;
    while (/\s/.test(input[cursor] || '')) cursor++;
    if (input[cursor] === '/') cursor++;
    return input[cursor] === '>' ? cursor : -1;
  }

  function rawTextTokenSplitPosition(source) {
    const input = text(source);
    let position = 0;
    const openTags = [];

    while (position < input.length) {
      const start = input.indexOf('<', position);
      if (start < 0) return -1;

      const activeRawTextTag = openTags[openTags.length - 1];
      if (activeRawTextTag && rawHtmlTextTags.has(activeRawTextTag)) {
        const closeEnd = rawTextClosingTagEnd(input, start, activeRawTextTag);
        if (closeEnd < 0) {
          position = start + 1;
          continue;
        }
        openTags.pop();
        const split = closeEnd + 1;
        if (openTags.length === 0) return split;
        position = split;
        continue;
      }

      if (input.startsWith('<!--', start)) {
        const commentEnd = input.indexOf('-->', start + 4);
        if (commentEnd < 0) return -1;
        const split = commentEnd + 3;
        if (openTags.length === 0 && split < input.length) return split;
        position = split;
        continue;
      }

      let cursor = start + 1;
      const closing = input[cursor] === '/';
      if (closing) cursor++;
      if (!/[A-Za-z]/.test(input[cursor] || '')) {
        position = start + 1;
        continue;
      }

      const nameStart = cursor++;
      while (/[A-Za-z0-9:-]/.test(input[cursor] || '')) cursor++;
      const nameEnd = cursor;
      const name = input.slice(nameStart, nameEnd).toLowerCase();
      const validTagBoundary = /[\s/>]/.test(input[cursor] || '');
      let quote = '';
      let end = -1;

      for (; cursor < input.length; cursor++) {
        const character = input[cursor];
        if (quote) {
          if (character === quote) quote = '';
          continue;
        }
        if (character === '"' || character === "'") {
          quote = character;
          continue;
        }
        if (character === '>') {
          end = cursor;
          break;
        }
      }

      if (end < 0) return -1;
      if (!validTagBoundary) {
        position = end + 1;
        continue;
      }
      const split = end + 1;
      if (closing) {
        const openTag = openTags.lastIndexOf(name);
        if (openTag >= 0) {
          openTags.length = openTag;
          if (openTags.length === 0) return split;
        } else if (openTags.length === 0) {
          return split;
        }
        position = split;
        continue;
      }
      if (rawHtmlVoidTags.has(name) || isSelfClosingTag(input, nameEnd, end)) {
        if (openTags.length === 0) return split;
        position = split;
        continue;
      }

      openTags.push(name);
      position = split;
    }

    return -1;
  }

  function lexTailWithDocumentLinks(source, marked, parseOptions, links) {
    if (typeof marked.Lexer === 'function') {
      const lexer = new marked.Lexer(parseOptions);
      lexer.tokens.links = links;
      return lexer.lex(source);
    }

    const tokens = marked.lexer(source, parseOptions);
    if (tokens.links) Object.assign(links, tokens.links);
    return tokens;
  }

  function splitRawTextHtmlTails(tokens, marked, parseOptions) {
    const links = tokens.links || Object.create(null);
    if (!tokens.links) tokens.links = links;

    for (let index = 0; index < tokens.length; index++) {
      const token = tokens[index];
      if (!token || token.type !== 'html' || !token.block) continue;

      const raw = text(token.raw || token.text);
      const split = rawTextTokenSplitPosition(raw);
      const tail = split < 0 ? '' : raw.slice(split);
      if (!/\S/.test(tail) || (findMathStart(tail) < 0 && !hasResolvableMarkdownReference(tail, links))) continue;

      token.raw = raw.slice(0, split);
      token.text = token.raw;
      const tailTokens = lexTailWithDocumentLinks(tail, marked, parseOptions, links);
      tokens.splice(index, 1, token, ...tailTokens);
    }
  }

  function updateRawHtmlContext(source, state) {
    const input = text(source);
    let position = 0;

    while (position < input.length) {
      const start = input.indexOf('<', position);
      if (start < 0) return;

      const activeRawTextTag = state.openTags[state.openTags.length - 1];
      if (activeRawTextTag && rawHtmlTextTags.has(activeRawTextTag)) {
        const rawTextCloseEnd = rawTextClosingTagEnd(input, start, activeRawTextTag);
        if (rawTextCloseEnd >= 0) {
          closeRawHtmlContext(state, activeRawTextTag);
          position = rawTextCloseEnd + 1;
        } else {
          position = start + 1;
        }
        continue;
      }

      if (input.startsWith('<!--', start)) {
        const commentEnd = input.indexOf('-->', start + 4);
        position = commentEnd < 0 ? input.length : commentEnd + 3;
        continue;
      }

      let cursor = start + 1;
      const closing = input[cursor] === '/';
      if (closing) cursor++;
      if (!/[A-Za-z]/.test(input[cursor] || '')) {
        position = start + 1;
        continue;
      }

      const nameStart = cursor++;
      while (/[A-Za-z0-9:-]/.test(input[cursor] || '')) cursor++;
      const nameEnd = cursor;
      const name = input.slice(nameStart, cursor).toLowerCase();
      const validTagBoundary = /[\s/>]/.test(input[cursor] || '');
      let quote = '';
      let end = -1;

      for (; cursor < input.length; cursor++) {
        const character = input[cursor];
        if (quote) {
          if (character === quote) {
            quote = '';
          }
          continue;
        }
        if (character === '"' || character === "'") {
          quote = character;
          continue;
        }
        if (character === '>') {
          end = cursor;
          break;
        }
      }

      if (end < 0) return;
      if (!validTagBoundary) {
        position = end + 1;
        continue;
      }
      const selfClosing = !closing && isSelfClosingTag(input, nameEnd, end);
      if (closing) {
        closeRawHtmlContext(state, name);
      } else if (!selfClosing && !rawHtmlVoidTags.has(name)) {
        state.openTags.push(name);
      }
      position = end + 1;
    }
  }

  function literalMath(token) {
    token.type = 'text';
    token.text = text(token.raw);
    token.escaped = false;
    delete token.display;
  }

  function disableUnsafeMath(value, inImage, htmlState) {
    const state = htmlState || { openTags: [], pendingMath: [] };
    if (Array.isArray(value)) {
      value.forEach(token => disableUnsafeMath(token, inImage, state));
      return;
    }
    if (!value || typeof value !== 'object') return;

    if (value.type === 'html') {
      updateRawHtmlContext(value.raw || value.text, state);
      return;
    }
    if (value.type === 'math' && inImage) {
      literalMath(value);
      return;
    }
    if (value.type === 'math' && state.openTags.length > 0) {
      state.pendingMath.push({ token: value, openTags: state.openTags.slice() });
      return;
    }
    if (value.type === 'image') {
      disableUnsafeMath(value.tokens, true, { openTags: [], pendingMath: [] });
      return;
    }

    for (const key of ['tokens', 'items', 'header', 'rows']) {
      if (value[key]) disableUnsafeMath(value[key], inImage, state);
    }
  }

  function imageAltText(tokens) {
    return (tokens || []).map(token => {
      if (!token || typeof token !== 'object') return '';
      if (token.type === 'text' || token.type === 'escape' || token.type === 'codespan') return text(token.text);
      if (token.type === 'math') return text(token.raw);
      if (token.type === 'html') return text(token.raw || token.text);
      return Array.isArray(token.tokens) ? imageAltText(token.tokens) : '';
    }).join('');
  }

  function extensionList(extensions, key) {
    return extensions && Array.isArray(extensions[key]) ? extensions[key] : [];
  }

  function mergeExtensions(hostExtensions, localExtensions) {
    const host = hostExtensions && typeof hostExtensions === 'object' ? hostExtensions : {};
    const local = localExtensions && typeof localExtensions === 'object' ? localExtensions : {};

    return {
      ...host,
      inline: [...extensionList(local, 'inline'), ...extensionList(host, 'inline')],
      startInline: [...extensionList(local, 'startInline'), ...extensionList(host, 'startInline')],
      renderers: { ...(host.renderers || {}), ...(local.renderers || {}) },
      childTokens: { ...(host.childTokens || {}), ...(local.childTokens || {}) },
    };
  }

  function restoreOwnProperty(object, key, descriptor) {
    if (descriptor) Object.defineProperty(object, key, descriptor);
    else delete object[key];
  }

  function createSafeRenderer(marked, hostRenderer) {
    if (!hostRenderer || typeof hostRenderer !== 'object') {
      return { renderer: new marked.Renderer(), restore() {} };
    }

    const renderer = Object.create(hostRenderer);
    const options = Object.getOwnPropertyDescriptor(hostRenderer, 'options');
    const parser = Object.getOwnPropertyDescriptor(hostRenderer, 'parser');
    Object.defineProperties(renderer, {
      options: {
        configurable: true,
        get() { return hostRenderer.options; },
        set(value) { hostRenderer.options = value; },
      },
      parser: {
        configurable: true,
        get() { return hostRenderer.parser; },
        set(value) { hostRenderer.parser = value; },
      },
    });

    return {
      renderer,
      restore() {
        restoreOwnProperty(hostRenderer, 'options', options);
        restoreOwnProperty(hostRenderer, 'parser', parser);
      },
    };
  }

  function createMathAwareTokenizer(marked, hostTokenizer) {
    const baseTokenizer = hostTokenizer || (typeof marked.Tokenizer === 'function' ? new marked.Tokenizer() : null);
    if (!baseTokenizer || typeof baseTokenizer.inlineText !== 'function') return baseTokenizer;

    const tokenizer = Object.create(baseTokenizer);
    const inlineText = tokenizer.inlineText;
    let nativeRules = null;
    let dollarAwareRules = null;
    tokenizer.inlineText = function dollarAwareInlineText(source) {
      const rules = this.rules;
      if (rules !== nativeRules) {
        nativeRules = rules;
        dollarAwareRules = null;
        const inlineRules = rules && rules.inline;
        const textRule = inlineRules && inlineRules.text;
        if (textRule) {
          const dollarAwareSource = textRule.source.replace('\\\\<!', '\\\\$<!');
          if (dollarAwareSource !== textRule.source) {
            dollarAwareRules = {
              ...rules,
              inline: {
                ...inlineRules,
                text: new RegExp(dollarAwareSource, textRule.flags),
              },
            };
          }
        }
      }
      if (!dollarAwareRules) return inlineText.call(this, source);

      this.rules = dollarAwareRules;
      try {
        return inlineText.call(this, source);
      } finally {
        this.rules = rules;
      }
    };
    return tokenizer;
  }

  function createMarkdownRenderer({ getMarked, getKatex } = {}) {
    function render(value) {
      const input = text(value);
      if (hasPathologicalEmphasisParagraph(input)) return escapeHtml(input);
      let katex = null;

      function renderMath(source, display) {
        if (!katex || typeof katex.renderToString !== 'function') return escapeHtml(source);
        try {
          return katex.renderToString(source, {
            displayMode: display,
            throwOnError: false,
            trust: false,
            maxExpand: 1000,
          });
        } catch (error) {
          return escapeHtml(source);
        }
      }

      let marked;
      try {
        marked = typeof getMarked === 'function' ? getMarked() : null;
      } catch (error) {
        return escapeHtml(input);
      }
      if (!marked || typeof marked.lexer !== 'function' || typeof marked.parser !== 'function' || typeof marked.Renderer !== 'function') return escapeHtml(input);

      try {
        const hostDefaults = marked.defaults || {};
        const safeRenderer = createSafeRenderer(marked, hostDefaults.renderer);
        const renderer = safeRenderer.renderer;
        let rejectedDisplayMathTail = '';
        const extensions = {
          inline: [
            function (source) { return malformedHtmlLikeToken(source, this.lexer); },
            function (source) {
              if (rejectedDisplayMathTail && source === rejectedDisplayMathTail) {
                rejectedDisplayMathTail = '';
                return { type: 'text', raw: '$$', text: '$$' };
              }
              return mathToken(source, this.lexer, tail => { rejectedDisplayMathTail = tail; });
            },
            function (source) { return markdownLinkLabelMathToken(source, this.lexer); },
          ],
          renderers: {
            html() { return false; },
            link() { return false; },
            image() { return false; },
            math(token) {
              return renderMath(token.text, token.display);
            },
          },
        };

        Object.defineProperties(renderer, {
          html: {
            configurable: true,
            writable: true,
            value: token => escapeHtml(token.raw || token.text),
          },
          link: {
            configurable: true,
            writable: true,
            value: function (token) {
              const body = this.parser.parseInline(token.tokens || []);
              const href = safeHref(token.href);
              if (!href) return body;
              const title = token.title ? ` title="${escapeHtml(token.title)}"` : '';
              return `<a href="${escapeHtml(href)}"${title}>${body}</a>`;
            },
          },
          image: {
            configurable: true,
            writable: true,
            value: token => escapeHtml(imageAltText(token.tokens)),
          },
        });

        const parseOptions = {
          ...hostDefaults,
          tokenizer: createMathAwareTokenizer(marked, hostDefaults.tokenizer),
          extensions: mergeExtensions(hostDefaults.extensions, extensions),
        };
        const tokens = marked.lexer(input, parseOptions);
        splitRawTextHtmlTails(tokens, marked, parseOptions);
        const htmlState = { openTags: [], pendingMath: [] };
        disableUnsafeMath(tokens, false, htmlState);
        htmlState.pendingMath.forEach(record => literalMath(record.token));
        try {
          katex = typeof getKatex === 'function' ? getKatex() : null;
        } catch (error) {
          katex = null;
        }
        try {
          return marked.parser(tokens, { ...parseOptions, renderer });
        } finally {
          safeRenderer.restore();
        }
      } catch (error) {
        return escapeHtml(input);
      }
    }

    function renderInto(element, value) {
      if (element) element.innerHTML = render(value);
      return element;
    }

    return { render, renderInto };
  }

  return { createMarkdownRenderer };
}));
