import os
import sys

def clean_filename(filename):
    # Split filename and extension
    name, ext = os.path.splitext(filename)
    
    # 1. Remove specific characters: ( ) [ ] ! +
    for char in "()[]!+":
        name = name.replace(char, "")
    
    # 2. Replace spaces with periods
    name = name.replace(" ", ".")
    
    # 3. Append -source if it's not already there
    if not name.endswith("-source"):
        name += "-source"
        
    return name + ext

def main():
    # Iterate over all files in the current working directory
    for filename in os.listdir('.'):
        # Process only .mkv files
        if filename.lower().endswith(".mkv"):
            
            # Skip files that end in -source, -av1, or -output
            # Passing a tuple to endswith allows checking multiple suffixes at once
            if filename.endswith(("-source.mkv", "-av1.mkv", "-output.mkv")):
                continue

            new_name = clean_filename(filename)
            
            # Only rename if the name has actually changed
            if new_name != filename:
                try:
                    os.rename(filename, new_name)
                    print(f"Renamed: '{filename}' -> '{new_name}'")
                except OSError as e:
                    print(f"Error renaming '{filename}': {e}")

if __name__ == "__main__":
    main()