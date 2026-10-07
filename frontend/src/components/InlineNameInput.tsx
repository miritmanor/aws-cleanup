// The shared name-editing input: Enter or blur commits, Escape cancels; unchanged
// or empty text is a cancel.

import { useState } from "react";

interface Props {
  initial: string;
  onCommit: (name: string) => void;
  onCancel: () => void;
}

export function InlineNameInput({ initial, onCommit, onCancel }: Props) {
  const [value, setValue] = useState(initial);

  function commit() {
    const name = value.trim();
    if (!name || name === initial) onCancel();
    else onCommit(name);
  }

  return (
    <input
      autoFocus
      className="name-input"
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") onCancel();
      }}
    />
  );
}
