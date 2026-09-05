#!/usr/bin/env bash
# Launch a uniquely named, temporary copy of this applet with a JSON fixture.
# It never changes the installed AI Usage package or the user's containment.
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
source_tree=${AI_USAGE_VISUAL_SOURCE_TREE:-"$repo_root/plasmoid"}
fixture=${1:?usage: plasma-visual-harness.sh FIXTURE.json OUTPUT_DIR [label]}
output_dir=${2:?usage: plasma-visual-harness.sh FIXTURE.json OUTPUT_DIR [label]}
label=${3:-visual}
helper_delay=${AI_USAGE_VISUAL_HELPER_DELAY_SECONDS:-0}
no_providers=${AI_USAGE_VISUAL_NO_PROVIDERS:-0}
presentation=${AI_USAGE_VISUAL_PRESENTATION:-full}
scratch_dir=$(mktemp -d /tmp/ai-usage-visual.XXXXXX)
package_dir="$scratch_dir/package"
log_file="$output_dir/$label.log"
capture_path="$scratch_dir/capture.png"
package_id=""
panel_id=""

cleanup() {
    if [[ -n ${window_pid:-} ]]; then
        kill "$window_pid" >/dev/null 2>&1 || true
        wait "$window_pid" >/dev/null 2>&1 || true
    fi
    if [[ -n "$package_id" ]]; then
        if [[ -n "$panel_id" ]]; then
            qdbus-qt6 org.kde.plasmashell /PlasmaShell \
                org.kde.PlasmaShell.evaluateScript \
                "var p = panelById($panel_id); if (p) p.remove();" >/dev/null 2>&1 || true
        fi
        kpackagetool6 -t Plasma/Applet -r "$package_id" >/dev/null 2>&1 || true
    fi
    rm -rf "$scratch_dir"
}
trap cleanup EXIT

mkdir -p "$output_dir"
cp -a "$source_tree" "$package_dir"
cp "$fixture" "$scratch_dir/fixture.json"
package_id="com.nicl9923.aiusage.visual.$(basename "$scratch_dir" | tr -cd '[:alnum:]')"
sed -i "s/com.nicl9923.aiusage/$package_id/" "$package_dir/metadata.json"
[[ "$helper_delay" =~ ^[0-9]+$ ]]
[[ "$presentation" == full || "$presentation" == horizontal || "$presentation" == vertical ]]
fixture_command="sh -c 'sleep $helper_delay; cat $scratch_dir/fixture.json'"
sed -i "s#\$HOME/.local/bin/ai-usage#$fixture_command#" "$package_dir/contents/config/main.xml"
if [[ "$no_providers" == 1 ]]; then
    for provider in Codex Claude Grok; do
        sed -i "/name=\"show$provider\"/,/<\\/entry>/ s#<default>true</default>#<default>false</default>#" \
            "$package_dir/contents/config/main.xml"
    done
fi
# Capture the item's pixels inside the fixture process. Unlike a compositor screenshot,
# this cannot include any other application or desktop content.
sed -i '/id: full/a\    Rectangle { anchors.fill: parent; z: -1; color: Kirigami.Theme.backgroundColor }' \
    "$package_dir/contents/ui/FullRepresentation.qml"
capture_target=root.fullRepresentationItem
if [[ "$presentation" != full ]]; then
    capture_target=root.compactRepresentationItem
fi
python3 -c 'import json, pathlib, sys; lines = ["import QtQuick", "Item {", "    required property Item target", "    visible: false", "    Timer {", "        interval: 6000", "        running: true", "        repeat: false", "        onTriggered: target.grabToImage(function(result) { result.saveToFile(" + json.dumps(sys.argv[1]) + ") })", "    }", "}"]; pathlib.Path(sys.argv[2]).write_text(chr(10).join(lines) + chr(10))' \
    "$capture_path" "$package_dir/contents/ui/CaptureHook.qml"
sed -i "$ i\\    CaptureHook { target: $capture_target }" \
    "$package_dir/contents/ui/main.qml"

kpackagetool6 -t Plasma/Applet -i "$package_dir" >/dev/null

if [[ "$presentation" == full ]]; then
    plasmawindowed "$package_id" >"$log_file" 2>&1 &
    window_pid=$!
else
    panel_location=top
    if [[ "$presentation" == vertical ]]; then
        panel_location=left
    fi
    panel_id=$(qdbus-qt6 org.kde.plasmashell /PlasmaShell \
        org.kde.PlasmaShell.evaluateScript \
        "var p = new Panel; p.location = \"$panel_location\"; p.height = 48; p.lengthMode = \"fit\"; p.alignment = \"center\"; p.addWidget(\"$package_id\"); print(p.id);" | tail -n1)
fi
for _ in {1..12}; do
    [[ -s "$capture_path" ]] && break
    sleep 1
done
[[ -s "$capture_path" ]]
cp "$capture_path" "$output_dir/$label.png"
echo "$output_dir/$label.png"
