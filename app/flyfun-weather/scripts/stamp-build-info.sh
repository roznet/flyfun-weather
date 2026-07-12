#!/bin/sh
#
# Writes BuildStamp.plist into the built app bundle: when this binary was
# compiled and from which git revision. Settings ▸ Developer reads it (BuildInfo)
# so a build installed on a device can be traced back to a commit.
#
# Nothing in the source tree is touched — the stamp lives only in the build
# product, so compiling never dirties the working tree.
#
# Build-phase wiring (see project.pbxproj):
#   input   $(TARGET_BUILD_DIR)/$(INFOPLIST_PATH)   — orders this phase *after*
#           ProcessInfoPlistFile, which otherwise runs late and would clobber a
#           stamp written into Info.plist itself.
#   output  $(TARGET_BUILD_DIR)/$(UNLOCALIZED_RESOURCES_FOLDER_PATH)/BuildStamp.plist
#
set -eu

out="${TARGET_BUILD_DIR}/${UNLOCALIZED_RESOURCES_FOLDER_PATH}/BuildStamp.plist"
mkdir -p "$(dirname "$out")"

build_date=$(date -u "+%Y-%m-%d %H:%M UTC")

branch="?"
commit="unknown"
if sha=$(git -C "$SRCROOT" rev-parse --short HEAD 2>/dev/null); then
    branch=$(git -C "$SRCROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
    # Dirty check is scoped to the app sources: an uncommitted change to the
    # Python backend doesn't make *this* binary any less reproducible.
    if [ -n "$(git -C "$SRCROOT" status --porcelain -- "$SRCROOT" 2>/dev/null)" ]; then
        commit="${sha}-dirty"
    else
        commit="$sha"
    fi
fi

cat > "$out" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>BuildDate</key>
	<string>${build_date}</string>
	<key>GitBranch</key>
	<string>${branch}</string>
	<key>GitCommit</key>
	<string>${commit}</string>
	<key>Configuration</key>
	<string>${CONFIGURATION}</string>
</dict>
</plist>
PLIST

echo "Build stamp: ${build_date} — ${branch} @ ${commit} (${CONFIGURATION})"
