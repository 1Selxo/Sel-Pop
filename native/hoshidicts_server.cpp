#include <glaze/glaze.hpp>

#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "hoshidicts/importer.hpp"
#include "hoshidicts/query.hpp"

struct JsonGlossary {
  std::string dictionary;
  std::string content;
  std::string definition_tags;
  std::string term_tags;
};

struct JsonFrequency {
  std::string dictionary;
  int value;
  std::string display_value;
};

struct JsonPitch {
  std::string dictionary;
  std::vector<int> positions;
  std::vector<std::string> transcriptions;
};

struct JsonTerm {
  std::string expression;
  std::string reading;
  std::string rules;
  int score;
  std::vector<JsonGlossary> glossaries;
  std::vector<JsonFrequency> frequencies;
  std::vector<JsonPitch> pitches;
};

struct JsonQueryResult {
  std::string query;
  std::vector<JsonTerm> terms;
};

struct JsonCommand {
  std::string type;
  std::string dictionary;
  std::string path;
};

std::string base64_encode(const std::vector<char>& input) {
  static constexpr char alphabet[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string output;
  output.reserve(((input.size() + 2) / 3) * 4);
  for (size_t i = 0; i < input.size(); i += 3) {
    const auto a = static_cast<unsigned char>(input[i]);
    const auto b = i + 1 < input.size() ? static_cast<unsigned char>(input[i + 1]) : 0;
    const auto c = i + 2 < input.size() ? static_cast<unsigned char>(input[i + 2]) : 0;
    const uint32_t value = (static_cast<uint32_t>(a) << 16) |
                           (static_cast<uint32_t>(b) << 8) | c;
    output.push_back(alphabet[(value >> 18) & 0x3f]);
    output.push_back(alphabet[(value >> 12) & 0x3f]);
    output.push_back(i + 1 < input.size() ? alphabet[(value >> 6) & 0x3f] : '=');
    output.push_back(i + 2 < input.size() ? alphabet[value & 0x3f] : '=');
  }
  return output;
}

struct JsonImportResult {
  bool success;
  std::string title;
  std::string path;
  size_t term_count;
  size_t meta_count;
  size_t frequency_count;
  size_t pitch_count;
  size_t media_count;
  std::vector<std::string> errors;
};

template <typename T>
std::string to_json(T&& value) {
  auto result = glz::write_json(std::forward<T>(value));
  return result ? std::move(*result) : "{\"error\":\"serialization failed\"}";
}

JsonTerm convert_term(TermResult&& source) {
  JsonTerm result{
      .expression = std::move(source.expression),
      .reading = std::move(source.reading),
      .rules = std::move(source.rules),
      .score = source.score,
  };
  for (auto& glossary : source.glossaries) {
    result.glossaries.push_back({
        .dictionary = std::move(glossary.dict_name),
        .content = std::move(glossary.glossary),
        .definition_tags = std::move(glossary.definition_tags),
        .term_tags = std::move(glossary.term_tags),
    });
  }
  for (auto& group : source.frequencies) {
    for (auto& frequency : group.frequencies) {
      result.frequencies.push_back({
          .dictionary = group.dict_name,
          .value = frequency.value,
          .display_value = std::move(frequency.display_value),
      });
    }
  }
  for (auto& pitch : source.pitches) {
    result.pitches.push_back({
        .dictionary = std::move(pitch.dict_name),
        .positions = std::move(pitch.pitch_positions),
        .transcriptions = std::move(pitch.transcriptions),
    });
  }
  return result;
}

int run_import(const std::string& zip_path, const std::string& output_dir) {
  auto imported = dictionary_importer::import(zip_path, output_dir, false);
  JsonImportResult result{
      .success = imported.success,
      .title = imported.title,
      .path = (std::filesystem::path(output_dir) / imported.title).string(),
      .term_count = imported.term_count,
      .meta_count = imported.meta_count,
      .frequency_count = imported.freq_count,
      .pitch_count = imported.pitch_count,
      .media_count = imported.media_count,
      .errors = std::move(imported.errors),
  };
  std::cout << to_json(result) << std::endl;
  return imported.success ? 0 : 1;
}

int run_server(int argc, char** argv) {
  DictionaryQuery query;
  for (int i = 1; i < argc; ++i) {
    query.add_term_dict(argv[i]);
    query.add_freq_dict(argv[i]);
    query.add_pitch_dict(argv[i]);
  }

  std::cout << "{\"ready\":true}" << std::endl;
  std::string expression;
  while (std::getline(std::cin, expression)) {
    if (expression.empty()) {
      std::cout << "[]" << std::endl;
      continue;
    }
    try {
      if (expression.front() == '{') {
        JsonCommand command;
        auto error = glz::read_json(command, expression);
        if (error || command.type != "media") {
          throw std::runtime_error("invalid command");
        }
        auto media = query.get_media_file(command.dictionary, command.path);
        std::cout << "{\"media\":" << to_json(base64_encode(media)) << "}" << std::endl;
        continue;
      }

      std::vector<std::string> expressions;
      if (expression.front() == '[') {
        auto error = glz::read_json(expressions, expression);
        if (error) {
          throw std::runtime_error("invalid query batch");
        }
      } else {
        expressions.push_back(expression);
      }

      std::vector<JsonQueryResult> output;
      output.reserve(expressions.size());
      for (auto& item : expressions) {
        auto terms = query.query(item);
        JsonQueryResult result{.query = std::move(item)};
        result.terms.reserve(terms.size());
        for (auto& term : terms) {
          result.terms.push_back(convert_term(std::move(term)));
        }
        output.push_back(std::move(result));
      }
      std::cout << to_json(output) << std::endl;
    } catch (const std::exception& error) {
      std::cout << "{\"error\":" << to_json(std::string(error.what())) << "}" << std::endl;
    }
  }
  return 0;
}

int main(int argc, char** argv) {
  std::ios::sync_with_stdio(false);
  if (argc == 4 && std::string_view(argv[1]) == "--import") {
    return run_import(argv[2], argv[3]);
  }
  return run_server(argc, argv);
}
