import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Copy, Inbox, X } from "lucide-react";
import { format } from "./types";

export function Select({
  value,
  options,
  onChange,
  label,
  id,
  disabled = false,
  placeholder = "请选择",
}: {
  value: string;
  options: { value: string; label: string; disabled?: boolean }[];
  onChange: (value: string) => void;
  label: string;
  id?: string;
  disabled?: boolean;
  placeholder?: string;
}) {
  const generatedId = useId();
  const listId = `${generatedId}-options`;
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const search = useRef({ text: "", at: 0 });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState({
    top: 0,
    left: 0,
    width: 0,
    maxHeight: 280,
  });
  const selected = options.find((item) => item.value === value);
  const available = options
    .map((item, index) => (item.disabled ? -1 : index))
    .filter((index) => index >= 0);

  function show() {
    const index = options.findIndex(
      (item) => item.value === value && !item.disabled,
    );
    setActive(index >= 0 ? index : (available[0] ?? 0));
    setOpen(true);
  }
  function choose(index: number) {
    const item = options[index];
    if (!item || item.disabled) return;
    onChange(item.value);
    setOpen(false);
    trigger.current?.focus();
  }
  useEffect(() => {
    if (!open) return;
    function place() {
      const rect = trigger.current!.getBoundingClientRect();
      const height = Math.min(280, options.length * 40 + 12);
      const below = innerHeight - rect.bottom - 12;
      const above = below < Math.min(height, 160) && rect.top > below;
      const maxHeight = Math.min(
        height,
        Math.max(80, above ? rect.top - 12 : below),
      );
      const width = Math.min(Math.max(rect.width, 160), innerWidth - 24);
      setPosition({
        top: above ? rect.top - maxHeight - 5 : rect.bottom + 5,
        left: Math.max(12, Math.min(rect.left, innerWidth - width - 12)),
        width,
        maxHeight,
      });
    }
    function outside(event: PointerEvent) {
      if (
        !trigger.current?.contains(event.target as Node) &&
        !menu.current?.contains(event.target as Node)
      )
        setOpen(false);
    }
    place();
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, options.length]);
  useEffect(() => {
    if (open)
      menu.current
        ?.querySelector(`[data-index="${active}"]`)
        ?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  return (
    <span className="select-control">
      <button
        ref={trigger}
        id={id || generatedId}
        type="button"
        className="select-trigger"
        role="combobox"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        disabled={disabled || !available.length}
        onClick={() => (open ? setOpen(false) : show())}
        onBlur={(event) => {
          if (!menu.current?.contains(event.relatedTarget)) setOpen(false);
        }}
        onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            if (!open) {
              show();
              return;
            }
            const next =
              event.key === "Home"
                ? available[0]
                : event.key === "End"
                  ? available.at(-1)
                  : available[
                      (available.indexOf(active) +
                        (event.key === "ArrowDown" ? 1 : -1) +
                        available.length) %
                        available.length
                    ];
            if (next !== undefined) setActive(next);
          } else if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (open) choose(active);
            else show();
          } else if (event.key === "Escape" && open) {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
          } else if (event.key === "Tab") setOpen(false);
          else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
            search.current = {
              text:
                Date.now() - search.current.at < 700
                  ? search.current.text + event.key
                  : event.key,
              at: Date.now(),
            };
            const index = options.findIndex(
              (item) =>
                !item.disabled &&
                item.label
                  .toLowerCase()
                  .startsWith(search.current.text.toLowerCase()),
            );
            if (index >= 0) {
              if (open) setActive(index);
              else onChange(options[index].value);
            }
          }
        }}
      >
        <span className={selected ? "" : "muted"}>
          {selected?.label || placeholder}
        </span>
        <ChevronDown size={16} className={open ? "rotated" : ""} />
      </button>
      {open &&
        !disabled &&
        createPortal(
          <div
            ref={menu}
            id={listId}
            role="listbox"
            aria-label={`${label}选项`}
            className="select-menu"
            style={position}
          >
            {options.map((item, index) => (
              <div
                key={item.value}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={item.value === value}
                aria-disabled={item.disabled || undefined}
                data-index={index}
                className={`select-option ${active === index ? "active" : ""}`}
                onPointerMove={() => {
                  if (!item.disabled) setActive(index);
                }}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(index)}
              >
                <span>{item.label}</span>
                {item.value === value && <Check size={15} />}
              </div>
            ))}
          </div>,
          document.body,
        )}
    </span>
  );
}

export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Inbox size={26} />
      </span>
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}

export function Badge({
  children,
  tone = "",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Modal({
  title,
  children,
  onClose,
  busy = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    dialog
      .querySelector<HTMLInputElement | HTMLTextAreaElement>(
        "input:not(:disabled), textarea:not(:disabled)",
      )
      ?.focus();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-heading">
        <h2 id={titleId}>{title}</h2>
        <button
          className="icon-button"
          aria-label="关闭对话框"
          disabled={busy}
          onClick={onClose}
        >
          <X size={18} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

export function JsonView({
  value,
  label = "原始数据",
}: {
  value: unknown;
  label?: string;
}) {
  const [copy, setCopy] = useState("");
  useEffect(() => {
    if (copy) {
      const timer = setTimeout(() => setCopy(""), 2500);
      return () => clearTimeout(timer);
    }
  }, [copy]);
  return (
    <div className="json-view">
      <div className="json-heading">
        <span>{label}</span>
        <button
          className="icon-button"
          aria-label={`复制${label}`}
          onClick={() => {
            void navigator.clipboard.writeText(format(value)).then(
              () => setCopy("已复制"),
              () => setCopy("复制失败，请手动选择"),
            );
          }}
        >
          {copy ? (
            <>
              <Check size={14} />
              <small>{copy}</small>
            </>
          ) : (
            <Copy size={14} />
          )}
        </button>
      </div>
      <pre>{format(value)}</pre>
    </div>
  );
}
