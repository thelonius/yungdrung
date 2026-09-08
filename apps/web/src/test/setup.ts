import '@testing-library/jest-dom/vitest';

// В jsdom нет прокрутки; компонентам она и не нужна, но вызов не должен падать.
Element.prototype.scrollIntoView ??= () => {};
