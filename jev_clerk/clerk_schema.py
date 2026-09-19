"""The closed vocabulary. System 2 may reword what these mean, never add to them."""

TYPE_KINDS = ("type_supplier", "type_date", "type_item", "type_amount")
FIXED_KINDS = ("click_item", *TYPE_KINDS, "press_return", "press_tab", "press_escape", "scroll_down", "scroll_up", "wait", "done")
ALLOWED_KEYS = ("escape", "tab", "return", "delete", "down", "up")
