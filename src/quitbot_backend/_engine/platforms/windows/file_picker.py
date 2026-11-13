"""Windows native file dialogs exposed as file-selection operations."""

from ...errors import ControlUnavailable, TargetMismatch


class FilePicker:
    def __init__(self, backend, owner, control):
        self.backend, self.owner, self.control = backend, owner, control
        self.NativeWindowHandle = control.NativeWindowHandle
        self.Name = control.Name

    def check(self):
        current = find_file_picker(self.backend, self.owner)
        if current is None or current.NativeWindowHandle != self.NativeWindowHandle:
            raise TargetMismatch("Native file picker changed")

    def set_path(self, path, *, expected_name=None, directory=False):
        self.check()
        field_id = "1152" if directory else "1001" if self.Name == "另存为" else "1148"
        fields = [
            c for c in self.backend.walk(self.control)
            if c.ControlTypeName == "EditControl" and c.AutomationId == field_id
        ]
        if len(fields) != 1 or fields[0].GetValuePattern() is None:
            raise ControlUnavailable("Native filename input is missing or ambiguous")
        value = fields[0].GetValuePattern()
        if expected_name is not None and value.Value != expected_name:
            raise TargetMismatch("Save picker does not identify the selected file")
        value.SetValue(str(path), waitTime=0.05)
        if value.Value != str(path):
            raise TargetMismatch("Native picker path verification failed")
        self.check()

    def set_files(self, paths):
        if len(paths) == 1:
            return self.set_path(paths[0])
        if not paths or any('"' in str(path) for path in paths):
            raise ValueError("Provide file paths that can be selected together")
        self.set_path(" ".join('"' + str(path) + '"' for path in paths))

    def _button(self, identifier):
        return self.backend.find(
            self.control,
            lambda c: c.ControlTypeName == "ButtonControl" and c.AutomationId == identifier,
        )

    def confirm(self):
        self.check()
        self.backend.invoke(self._button("1"))

    def cancel(self):
        self.check()
        self.backend.invoke(self._button("2"))


def find_file_picker(backend, owner):
    matches = [
        c for c in backend.walk(owner)
        if c.ControlTypeName == "WindowControl" and c.ClassName == "#32770"
    ]
    if len(matches) > 1:
        raise ControlUnavailable("Multiple native file dialogs are present")
    return FilePicker(backend, owner, matches[0]) if matches else None
