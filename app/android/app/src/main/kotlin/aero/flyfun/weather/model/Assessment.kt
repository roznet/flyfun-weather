package aero.flyfun.weather.model

import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder

@Serializable(with = AssessmentSerializer::class)
enum class Assessment(val wireValue: String, val label: String) {
    GREEN("green", "Green"),
    AMBER("amber", "Amber"),
    RED("red", "Red"),
    UNAVAILABLE("unavailable", "N/A");

    companion object {
        fun fromString(s: String?): Assessment {
            if (s.isNullOrBlank()) return UNAVAILABLE
            val lower = s.trim().lowercase()
            return entries.firstOrNull { it.wireValue == lower } ?: UNAVAILABLE
        }

        fun fromWire(s: String?): Assessment = fromString(s)

        /**
         * Sort rank for advisory status strings — RED first (0), then AMBER (1), GREEN (2), other (3).
         */
        fun severityRank(status: String): Int = when (status.trim().lowercase()) {
            "red" -> 0
            "amber" -> 1
            "green" -> 2
            else -> 3
        }
    }
}

object AssessmentSerializer : KSerializer<Assessment> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("aero.flyfun.weather.model.Assessment", PrimitiveKind.STRING)

    override fun serialize(encoder: Encoder, value: Assessment) {
        encoder.encodeString(value.wireValue)
    }

    override fun deserialize(decoder: Decoder): Assessment =
        Assessment.fromString(runCatching { decoder.decodeString() }.getOrNull())
}
