import { describe, expect, it } from 'vitest';
import { parse } from './fieldPath';

describe('fieldPath.parse', () => {
  it('вложенный путь с двумя индексами', () => {
    expect(parse('steps[1].steps[0].control_date')).toEqual({ indices: [1, 0], leaf: 'control_date' });
  });

  it('элемент простого списка', () => {
    expect(parse('tags[1]')).toEqual({ indices: [1], leaf: 'tags' });
  });

  it('поле объекта в списке', () => {
    expect(parse('mentions[0].title')).toEqual({ indices: [0], leaf: 'title' });
  });

  it('поле верхнего уровня без индексов', () => {
    expect(parse('title')).toEqual({ indices: [], leaf: 'title' });
  });
});
