//
//  Item.swift
//  flyfun-weather
//
//  Created by Brice Rosenzweig on 01/03/2026.
//

import Foundation
import SwiftData

@Model
final class Item {
    var timestamp: Date
    
    init(timestamp: Date) {
        self.timestamp = timestamp
    }
}
