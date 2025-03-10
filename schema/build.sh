#!/usr/bin/env bash
./protoc --python_out=. all_pages.proto
./protoc --python_out=. all_requests.proto
./protoc --python_out=. all_parsed_css.proto
./protoc --python_out=. script_chunks.proto
