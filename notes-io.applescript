-- notes-io.applescript — read or overwrite the body of the mylife note.
--
--   get:  osascript notes-io.applescript get <noteName> <noteId|"">
--         -> prints the current body, or "__MISSING__" if the note doesn't exist.
--   set:  osascript notes-io.applescript set <noteName> <noteId|""> <fullHtml>
--         -> overwrites the body (creating the note if absent); prints the note id.
--
-- The TS caller does TRUE APPEND: it reads the LIVE body (so anything you deleted
-- in the note stays deleted), adds the new entry, and writes the result back.
-- Arbitrary text rides in as an argv item, so no shell/AppleScript escaping is needed.

on run argv
    set mode to item 1 of argv
    set noteName to item 2 of argv
    set noteId to item 3 of argv

    tell application "Notes"
        set theNote to missing value
        if noteId is not "" then
            try
                set theNote to note id noteId
            end try
        end if
        if theNote is missing value then
            if (exists note noteName) then set theNote to note noteName
        end if

        if mode is "get" then
            if theNote is missing value then return "__MISSING__"
            return (body of theNote)
        else
            set fullHtml to item 4 of argv
            if theNote is missing value then
                set theNote to make new note with properties {body:fullHtml}
            else
                set body of theNote to fullHtml
            end if
            return (id of theNote)
        end if
    end tell
end run
