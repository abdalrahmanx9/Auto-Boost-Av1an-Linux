import os
import sys


def parse_settings_lines(lines):
    settings = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";", "[")) or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        settings[key.strip().lower()] = value.strip()
    return settings


def set_settings_value(settings_path, key, value):
    key_l = key.lower()
    lines = []
    found = False
    if os.path.exists(settings_path):
        with open(settings_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";", "[")) or "=" not in line:
            continue
        existing_key, _ = line.split("=", 1)
        if existing_key.strip().lower() == key_l:
            lines[idx] = f"{existing_key.strip()}={value}"
            found = True
            break

    if not found:
        insert_at = 0
        while insert_at < len(lines) and lines[insert_at].strip().startswith("#"):
            insert_at += 1
        lines.insert(insert_at, f"{key}={value}")

    with open(settings_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")


def resolve_storage_path(path_text, root_dir):
    path_text = (path_text or "").strip().strip('"')
    if not path_text:
        path_text = os.path.join("tools", "ntfy.txt")
    if os.path.isabs(path_text) or (len(path_text) >= 3 and path_text[1:3] in (":\\", ":/")):
        return path_text
    if os.name != "nt":
        path_text = path_text.replace("\\", "/")
    return os.path.join(root_dir, path_text)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    settings_path = os.path.join(root_dir, "settings.txt")

    print("========================================================")
    print("                 Auto-Boost ntfy Setup")
    print("========================================================")
    print()
    print("Auto-Boost-Av1an Portable can notify your phone when encoding is complete.")
    print()
    print("Install ntfy on your Android or iPhone before continuing.")
    print("Notice: iPhone users may need to disable battery optimizations in settings to receive notifications")
    print()
    print("In the ntfy app on your phone:")
    print("  1. Add a new subscription.")
    print("  2. Input your secret word as the subscription topic.")
    print()
    print("This setup will save that same secret word and this PC name into a local ntfy.txt file.")
    print("Auto-Boost will read settings.txt for the ntfy.txt location, then read secretword and pcname from that file.")
    print()

    secret_word = ""
    while not secret_word:
        secret_word = input("Enter your ntfy secret word: ").strip()
        if not secret_word:
            print("Please enter a non-empty secret word.")

    pc_name = ""
    while not pc_name:
        pc_name = input("Give this PC a name: ").strip()
        if not pc_name:
            print("Please enter a non-empty PC name.")

    print()
    print("Where should the ntfy settings be stored?")
    print(r"Example: c:\ntfy.txt")
    print("Press Enter to use the default: tools/ntfy.txt")
    storage_input = input("ntfy.txt path: ").strip().strip('"')
    settings_value = storage_input or "tools/ntfy.txt"
    storage_path = resolve_storage_path(settings_value, root_dir)

    try:
        storage_dir = os.path.dirname(storage_path)
        if storage_dir:
            os.makedirs(storage_dir, exist_ok=True)
        with open(storage_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(f"secretword={secret_word}\n")
            f.write(f"pcname={pc_name}\n")
    except Exception as e:
        print()
        print(f"ERROR: Could not write ntfy secret word file: {e}")
        return 1

    try:
        set_settings_value(settings_path, "ntfy", settings_value)
    except Exception as e:
        print()
        print(f"ERROR: Could not update settings.txt: {e}")
        return 1

    print()
    print("ntfy setup complete.")
    print(f"ntfy settings file: {storage_path}")
    print(f"settings.txt updated with: ntfy={settings_value}")
    print()
    print("You should now receive ntfy notifications when an encode completes or fails.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
