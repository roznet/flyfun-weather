//
//  CrossSectionSharedStateTests.swift
//  flyfun-weatherTests
//
//  Parent suite for every test that builds a CrossSectionViewModel. The view
//  model reads and writes shared UserDefaults keys and the module-level
//  `CrossSectionTheme._active` global, and `.serialized` on a single suite only
//  orders that suite's own tests — sibling suites still run in parallel, and a
//  main-actor hop between one suite's `init()` (clearing the keys) and its test
//  body lets another suite's persisted theme or lens land in between. Nesting
//  the suites here serializes them against each other: the trait applies
//  recursively to nested suites.
//

import Testing

@MainActor
@Suite(.serialized) enum CrossSectionSharedStateTests {}
