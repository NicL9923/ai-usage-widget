/*
 * A circular progress indicator sized for the panel.
 *
 * Uses QtQuick.Shapes rather than Canvas so the arc is rendered on the GPU and does not
 * repaint on the UI thread every tick.
 */
import QtQuick
import QtQuick.Shapes

Item {
    id: root

    property real value: 0
    property color ringColor: "white"
    property color trackColor: ringColor
    property real trackOpacity: 0.25
    property real thickness: Math.max(2, Math.min(width, height) * 0.16)

    readonly property real _radius: (Math.min(width, height) - thickness) / 2
    readonly property real _sweep: 360 * Math.max(0, Math.min(100, value)) / 100

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer

        ShapePath {
            strokeColor: Qt.rgba(root.trackColor.r, root.trackColor.g, root.trackColor.b, root.trackOpacity)
            strokeWidth: root.thickness
            fillColor: "transparent"
            capStyle: ShapePath.FlatCap

            PathAngleArc {
                centerX: root.width / 2
                centerY: root.height / 2
                radiusX: root._radius
                radiusY: root._radius
                startAngle: -90
                sweepAngle: 360
            }
        }

        ShapePath {
            strokeColor: root.ringColor
            strokeWidth: root.thickness
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap

            PathAngleArc {
                centerX: root.width / 2
                centerY: root.height / 2
                radiusX: root._radius
                radiusY: root._radius
                startAngle: -90
                sweepAngle: root._sweep

                Behavior on sweepAngle {
                    NumberAnimation { duration: 400; easing.type: Easing.OutCubic }
                }
            }
        }
    }
}
